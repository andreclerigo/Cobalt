from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import Settings
from .models import CollectionSummary, ItemDetail, ItemSummary, Snapshot

PAGE_SIZE = 100
MAX_ATTACHMENTS_SCANNED = 2_000
MAX_TAGS = 12
MAX_AUTHORS = 32
MAX_ZOTERO_JSON_BYTES = 4 * 1024 * 1024
DETAIL_AUTHORIZATION_TTL_SECONDS = 5 * 60


class ZoteroError(RuntimeError):
    pass


class InvalidUpstream(ZoteroError):
    pass


@dataclass(frozen=True)
class PdfAttachment:
    key: str
    version: int


class ZoteroClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        self._owns_client = client is None
        self._semaphore = asyncio.Semaphore(4)
        self._backoff_until = 0.0
        self._snapshot_cache: dict[tuple[str, int], Snapshot] = {}
        self._detail_cache: dict[str, tuple[float, ItemDetail]] = {}

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def prefix(self) -> str:
        return f"{self._settings.zotero_base_url}/users/{self._settings.zotero_user_id}"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Zotero-API-Key": self._settings.zotero_api_key.get_secret_value(),
            "Zotero-API-Version": "3",
            "Accept": "application/json",
        }

    async def _request(
        self,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        for attempt in range(3):
            delay = self._backoff_until - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            async with self._semaphore:
                response = await _bounded_response(
                    self._client,
                    f"{self.prefix}{path}",
                    maximum=MAX_ZOTERO_JSON_BYTES,
                    headers={**self.headers, **(headers or {})},
                    params=params,
                )
            requested = response.headers.get("Backoff") or response.headers.get("Retry-After")
            if requested and requested.isdigit():
                self._backoff_until = max(
                    self._backoff_until, time.monotonic() + min(int(requested), 300)
                )
            if response.status_code != 429:
                break
            if attempt == 2:
                raise ZoteroError("Zotero rate limit did not clear")
        if response.status_code == 404:
            raise InvalidUpstream("Zotero item or collection was not found")
        if response.status_code == 304:
            return response
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise ZoteroError(f"Zotero returned HTTP {response.status_code}") from error
        return response

    async def collections(self) -> list[CollectionSummary]:
        allowed = set(self._settings.reading_list_collection_keys)
        collections: list[CollectionSummary] = []
        start = 0
        while start < MAX_ATTACHMENTS_SCANNED and len(collections) < len(allowed):
            response = await self._request(
                "/collections", params={"limit": PAGE_SIZE, "start": start}
            )
            rows = _json_list(response)
            for row in rows:
                key = _text(row.get("key"), 32)
                data = _object(row.get("data"))
                if key in allowed:
                    collections.append(
                        CollectionSummary(key=key, name=_text(data.get("name"), 160))
                    )
            if len(rows) < PAGE_SIZE:
                break
            start += len(rows)
        collections.sort(key=lambda collection: collection.name.casefold())
        return collections

    async def snapshot(self, collection_key: str, limit: int) -> Snapshot:
        self._require_collection(collection_key)
        cache_key = (collection_key, limit)
        cached = self._snapshot_cache.get(cache_key)
        rows: list[dict[str, Any]] = []
        total = 0
        revision = "0"
        start = 0
        while len(rows) < limit:
            response = await self._request(
                f"/collections/{collection_key}/items/top",
                params={
                    "limit": min(PAGE_SIZE, limit - len(rows)),
                    "start": start,
                    "sort": "dateAdded",
                    "direction": "desc",
                    "itemType": "-attachment",
                },
                headers=(
                    {"If-Modified-Since-Version": cached.revision}
                    if start == 0 and cached is not None
                    else None
                ),
            )
            if start == 0 and response.status_code == 304 and cached is not None:
                return cached.model_copy(deep=True)
            page = _json_list(response)
            if start == 0:
                total = _positive_header(response, "Total-Results", len(page))
                revision = response.headers.get("Last-Modified-Version", "0")[:32]
            rows.extend(page)
            if len(page) < min(PAGE_SIZE, limit - start):
                break
            start += len(page)
        item_keys = {_text(row.get("key"), 32) for row in rows}
        pdf_parents = await self._pdf_parents(collection_key, item_keys)
        items = [_summary(row, _text(row.get("key"), 32) in pdf_parents) for row in rows]
        items.sort(key=lambda item: item.date_added, reverse=True)
        snapshot = Snapshot(
            collection_key=collection_key,
            revision=revision,
            total=total,
            truncated=total > len(items),
            items=items,
        )
        _bounded_put(self._snapshot_cache, cache_key, snapshot)
        return snapshot

    async def detail(self, item_key: str) -> ItemDetail:
        cached = self._detail_cache.get(item_key)
        if cached is not None and cached[0] > time.monotonic():
            return cached[1].model_copy(deep=True)
        row = await self._allowed_item(item_key)
        attachment = await self._pdf_attachment(item_key)
        summary = _summary(row, attachment is not None)
        data = _object(row.get("data"))
        detail = ItemDetail(
            **summary.model_dump(),
            authors=_authors(data),
            abstract=_text(data.get("abstractNote"), 16_000),
            venue=_text(data.get("publicationTitle") or data.get("proceedingsTitle"), 512),
            doi=_text(data.get("DOI"), 512),
            url=_https_url(data.get("url")),
            pdf_attachment_key=attachment.key if attachment else None,
            pdf_attachment_version=attachment.version if attachment else None,
        )
        _bounded_put(
            self._detail_cache,
            item_key,
            (time.monotonic() + DETAIL_AUTHORIZATION_TTL_SECONDS, detail),
        )
        return detail

    async def ensure_allowed(self, item_key: str) -> None:
        await self._allowed_item(item_key)

    async def _allowed_item(self, item_key: str) -> dict[str, Any]:
        if not _valid_key(item_key):
            raise InvalidUpstream("invalid item key")
        response = await self._request(f"/items/{item_key}")
        row = _json_object(response)
        if _text(row.get("key"), 32) != item_key:
            raise InvalidUpstream("Zotero returned the wrong item key")
        collections = _object(row.get("data")).get("collections")
        allowed = set(self._settings.reading_list_collection_keys)
        if not isinstance(collections, list) or not allowed.intersection(collections):
            raise InvalidUpstream("item is outside the allowed collections")
        return row

    async def download_pdf(self, attachment: PdfAttachment) -> bytes:
        status_code, response_headers, body = await self._download(
            f"{self.prefix}/items/{attachment.key}/file", self.headers
        )
        if status_code in {301, 302, 303, 307, 308}:
            location = response_headers.get("Location", "")
            candidates = await _require_public_https(location)
            last_error: httpx.TransportError | None = None
            for pinned in candidates:
                try:
                    status_code, _, body = await self._download(
                        pinned.url,
                        {"Host": pinned.host_header},
                        extensions={"sni_hostname": pinned.hostname},
                    )
                    break
                except httpx.TransportError as error:
                    last_error = error
            else:
                raise ZoteroError("Zotero file redirect was unreachable") from last_error
        if status_code < 200 or status_code >= 300:
            raise ZoteroError(f"Zotero file download returned HTTP {status_code}")
        if not body.startswith(b"%PDF-"):
            raise InvalidUpstream("stored attachment is not a PDF")
        return body

    async def _download(
        self,
        url: str,
        headers: Mapping[str, str] | None,
        *,
        extensions: Mapping[str, Any] | None = None,
    ) -> tuple[int, httpx.Headers, bytes]:
        body = bytearray()
        delay = self._backoff_until - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        async with (
            self._semaphore,
            self._client.stream(
                "GET",
                url,
                headers=headers,
                extensions=extensions,
                follow_redirects=False,
                timeout=60.0,
            ) as response,
        ):
            requested = response.headers.get("Backoff") or response.headers.get("Retry-After")
            if requested and requested.isdigit():
                self._backoff_until = max(
                    self._backoff_until,
                    time.monotonic() + min(int(requested), 300),
                )
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > self._settings.max_pdf_bytes:
                raise InvalidUpstream("stored PDF is larger than the configured limit")
            if response.status_code not in {301, 302, 303, 307, 308}:
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self._settings.max_pdf_bytes:
                        raise InvalidUpstream("stored PDF is larger than the configured limit")
            return response.status_code, response.headers, bytes(body)

    async def _pdf_attachment(self, item_key: str) -> PdfAttachment | None:
        start = 0
        while start < MAX_ATTACHMENTS_SCANNED:
            response = await self._request(
                f"/items/{item_key}/children",
                params={"limit": PAGE_SIZE, "start": start},
            )
            rows = _json_list(response)
            for row in rows:
                attachment = _attachment(row)
                if attachment is not None:
                    return attachment
            if len(rows) < PAGE_SIZE:
                break
            start += len(rows)
        return None

    async def _pdf_parents(self, collection_key: str, wanted: set[str]) -> set[str]:
        parents: set[str] = set()
        start = 0
        while start < MAX_ATTACHMENTS_SCANNED and parents != wanted:
            response = await self._request(
                f"/collections/{collection_key}/items",
                params={"limit": PAGE_SIZE, "start": start, "itemType": "attachment"},
            )
            rows = _json_list(response)
            for row in rows:
                data = _object(row.get("data"))
                if _is_stored_pdf(data):
                    parent = _text(data.get("parentItem"), 32)
                    if parent in wanted:
                        parents.add(parent)
            if len(rows) < PAGE_SIZE:
                break
            start += len(rows)
        return parents

    def _require_collection(self, key: str) -> None:
        if key not in self._settings.reading_list_collection_keys:
            raise InvalidUpstream("collection is not allowed")


def _summary(row: dict[str, Any], has_pdf: bool) -> ItemSummary:
    key = _text(row.get("key"), 32)
    if not _valid_key(key):
        raise InvalidUpstream("Zotero returned an invalid item key")
    data = _object(row.get("data"))
    creators = _authors(data)
    date = _text(data.get("date"), 64)
    year = next(
        (
            date[index : index + 4]
            for index in range(max(len(date) - 3, 0))
            if date[index : index + 4].isdigit()
        ),
        "",
    )
    tags = []
    raw_tags = data.get("tags")
    if isinstance(raw_tags, list):
        for raw in raw_tags[:MAX_TAGS]:
            tag = _text(_object(raw).get("tag"), 48)
            if tag:
                tags.append(tag)
    return ItemSummary(
        key=key,
        version=_integer(row.get("version")),
        title=_text(data.get("title"), 512) or "Untitled",
        creator_summary=_text(", ".join(creators[:3]), 384),
        year=year,
        date_added=_text(data.get("dateAdded"), 64),
        tags=tags,
        has_stored_pdf=has_pdf,
    )


def _authors(data: dict[str, Any]) -> list[str]:
    authors: list[str] = []
    raw = data.get("creators")
    if not isinstance(raw, list):
        return authors
    for creator in raw[:MAX_AUTHORS]:
        fields = _object(creator)
        name = _text(fields.get("name"), 256)
        if not name:
            name = " ".join(
                part
                for part in (
                    _text(fields.get("firstName"), 128),
                    _text(fields.get("lastName"), 128),
                )
                if part
            )
        if name:
            authors.append(_text(name, 128))
    return authors


def _attachment(row: dict[str, Any]) -> PdfAttachment | None:
    data = _object(row.get("data"))
    if not _is_stored_pdf(data):
        return None
    key = _text(row.get("key"), 32)
    return PdfAttachment(key=key, version=_integer(row.get("version"))) if _valid_key(key) else None


def _is_stored_pdf(data: dict[str, Any]) -> bool:
    return data.get("contentType") == "application/pdf" and data.get("linkMode") in {
        "imported_file",
        "imported_url",
    }


@dataclass(frozen=True)
class PinnedHttps:
    url: str
    hostname: str
    host_header: str


async def _require_public_https(url: str) -> tuple[PinnedHttps, ...]:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise InvalidUpstream("Zotero returned an unsafe file redirect")
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo, parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
        )
    except socket.gaierror as error:
        raise InvalidUpstream("Zotero file redirect did not resolve") from error
    vetted: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise InvalidUpstream("Zotero file redirect resolved to a private address")
        if ip not in vetted:
            vetted.append(ip)
    if not vetted:
        raise InvalidUpstream("Zotero file redirect did not resolve")
    port = parsed.port or 443
    host_header = parsed.hostname if port == 443 else f"{parsed.hostname}:{port}"
    candidates = []
    for selected in vetted:
        selected_address = f"[{selected}]" if selected.version == 6 else str(selected)
        netloc = selected_address if port == 443 else f"{selected_address}:{port}"
        candidates.append(
            PinnedHttps(
                url=parsed._replace(netloc=netloc).geturl(),
                hostname=parsed.hostname,
                host_header=host_header,
            )
        )
    return tuple(candidates)


async def _bounded_response(
    client: httpx.AsyncClient,
    url: str,
    *,
    maximum: int,
    headers: Mapping[str, str],
    params: Mapping[str, str | int] | None,
) -> httpx.Response:
    body = bytearray()
    async with client.stream("GET", url, headers=headers, params=params) as response:
        length = response.headers.get("Content-Length", "")
        if length.isdigit() and int(length) > maximum:
            raise InvalidUpstream("Zotero returned an oversized response")
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > maximum:
                raise InvalidUpstream("Zotero returned an oversized response")
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=bytes(body),
            request=response.request,
        )


def _https_url(value: object) -> str:
    text = _text(value, 1_024)
    parsed = urlparse(text)
    return text if parsed.scheme == "https" and parsed.hostname and not parsed.username else ""


def _valid_key(value: str) -> bool:
    return bool(value) and len(value) <= 32 and value.isalnum()


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("\x00", "").split())[:limit]


def _integer(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError as error:
        raise InvalidUpstream("Zotero returned invalid JSON") from error
    if not isinstance(value, dict):
        raise InvalidUpstream("Zotero returned an unexpected object")
    return value


def _json_list(response: httpx.Response) -> list[dict[str, Any]]:
    try:
        value = response.json()
    except ValueError as error:
        raise InvalidUpstream("Zotero returned invalid JSON") from error
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise InvalidUpstream("Zotero returned an unexpected collection")
    return value


def _positive_header(response: httpx.Response, name: str, fallback: int) -> int:
    value = response.headers.get(name, "")
    return int(value) if value.isdigit() else fallback


def _bounded_put[K, V](target: dict[K, V], key: K, value: V, maximum: int = 512) -> None:
    if key not in target and len(target) >= maximum:
        target.pop(next(iter(target)))
    target[key] = value
