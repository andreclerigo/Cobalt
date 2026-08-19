from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from papers_bridge.config import Settings
from papers_bridge.models import ItemSummary, Snapshot
from papers_bridge.zotero import InvalidUpstream, PdfAttachment, ZoteroClient


def response(status: int, payload: Any, **headers: str) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(payload), headers=headers)


@pytest.mark.asyncio
async def test_snapshot_is_sorted_bounded_and_marks_stored_pdfs(settings: Settings) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/items/top"):
            if request.headers.get("If-Modified-Since-Version") == "42":
                return httpx.Response(304)
            return response(
                200,
                [
                    item("OLD", 1, "Older", "2026-01-01T00:00:00Z"),
                    item("NEW", 2, "Newer", "2026-02-01T00:00:00Z"),
                ],
                **{"Total-Results": "3", "Last-Modified-Version": "42"},
            )
        if request.url.path.endswith("/items"):
            return response(200, [attachment("PDF1", "NEW")])
        raise AssertionError(request.url)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    zotero = ZoteroClient(settings, client)

    snapshot = await zotero.snapshot("COLLECTION1", 2)
    cached = await zotero.snapshot("COLLECTION1", 2)

    assert [entry.key for entry in snapshot.items] == ["NEW", "OLD"]
    assert snapshot.items[0].has_stored_pdf
    assert not snapshot.items[1].has_stored_pdf
    assert snapshot.revision == "42"
    assert snapshot.truncated
    assert cached == snapshot
    assert len([call for call in calls if call.url.path.endswith("/items")]) == 1
    assert all(request.method == "GET" for request in calls)
    await client.aclose()


@pytest.mark.asyncio
async def test_detail_rejects_an_item_outside_allowed_collections(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return response(
            200,
            {**item("ITEM1", 1, "Paper", "2026-01-01"), "data": {"collections": []}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    zotero = ZoteroClient(settings, client)

    with pytest.raises(InvalidUpstream, match="outside"):
        await zotero.detail("ITEM1")
    await client.aclose()


@pytest.mark.asyncio
async def test_detail_rejects_a_mismatched_or_unsafe_upstream_key(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return response(
            200,
            {**item("../../outside", 1, "Paper", "2026-01-01")},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    zotero = ZoteroClient(settings, client)

    with pytest.raises(InvalidUpstream, match="wrong item key"):
        await zotero.detail("ITEM1")
    await client.aclose()


@pytest.mark.asyncio
async def test_detail_authorization_and_attachment_are_cached_for_bounded_polling(
    settings: Settings,
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/children"):
            return response(200, [attachment("PDF1", "ITEM1")])
        return response(200, item("ITEM1", 1, "Paper", "2026-01-01"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    zotero = ZoteroClient(settings, client)

    first = await zotero.detail("ITEM1")
    second = await zotero.detail("ITEM1")

    assert first == second
    assert len(calls) == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_download_accepts_only_bounded_pdf_bytes(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not a pdf")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    zotero = ZoteroClient(settings, client)

    with pytest.raises(InvalidUpstream, match="not a PDF"):
        await zotero.download_pdf(PdfAttachment("PDF1", 1))
    await client.aclose()


@pytest.mark.asyncio
async def test_download_rejects_private_redirects(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://127.0.0.1/file.pdf"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    zotero = ZoteroClient(settings, client)

    with pytest.raises(InvalidUpstream, match="private"):
        await zotero.download_pdf(PdfAttachment("PDF1", 1))
    await client.aclose()


@pytest.mark.asyncio
async def test_public_redirect_is_pinned_to_the_vetted_address(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[httpx.Request] = []

    def resolve(*_: object, **__: object) -> list[tuple[object, ...]]:
        return [
            (object(), object(), object(), "", ("2001:4860:4860::8888", 443)),
            (object(), object(), object(), "", ("93.184.216.34", 443)),
        ]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(302, headers={"Location": "https://files.example/paper.pdf"})
        if request.url.host == "2001:4860:4860::8888":
            raise httpx.ConnectError("no IPv6 route", request=request)
        assert request.url.host == "93.184.216.34"
        assert request.headers["Host"] == "files.example"
        assert request.extensions["sni_hostname"] == "files.example"
        return httpx.Response(200, content=b"%PDF-1.7")

    monkeypatch.setattr("papers_bridge.zotero.socket.getaddrinfo", resolve)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    zotero = ZoteroClient(settings, client)

    assert await zotero.download_pdf(PdfAttachment("PDF1", 1)) == b"%PDF-1.7"
    await client.aclose()


@pytest.mark.asyncio
async def test_rate_limit_is_retried_without_writes(settings: Settings) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return response(429, {}, **{"Retry-After": "0"})
        return response(200, [{"key": "COLLECTION1", "data": {"name": "Reading"}}])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    zotero = ZoteroClient(settings, client)

    assert (await zotero.collections())[0].name == "Reading"
    assert len(calls) == 2
    assert all(call.method == "GET" for call in calls)
    await client.aclose()


@pytest.mark.asyncio
async def test_oversized_zotero_json_is_rejected_before_parsing(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": str(4 * 1024 * 1024 + 1)},
            content=b"[]",
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    zotero = ZoteroClient(settings, client)

    with pytest.raises(InvalidUpstream, match="oversized"):
        await zotero.collections()
    await client.aclose()


def test_worst_case_500_item_snapshot_fits_cobalt_transport() -> None:
    item = ItemSummary(
        key="A" * 32,
        version=2**31,
        title="😀" * 512,
        creator_summary="😀" * 384,
        year="2026",
        date_added="2026-01-01T00:00:00Z",
        tags=["😀" * 48 for _ in range(12)],
        has_stored_pdf=True,
    )
    snapshot = Snapshot(
        collection_key="C" * 32,
        revision="9" * 32,
        total=500,
        truncated=False,
        items=[item] * 500,
    )

    assert len(snapshot.model_dump_json().encode()) <= 4 * 1024 * 1024


def test_worst_case_unicode_detail_fits_cobalt_transport() -> None:
    summary = ItemSummary(
        key="A" * 32,
        version=2**31,
        title="😀" * 512,
        creator_summary="😀" * 384,
        year="2026",
        date_added="2026-01-01T00:00:00Z",
        tags=["😀" * 48 for _ in range(12)],
        has_stored_pdf=True,
    )
    detail = {
        **summary.model_dump(),
        "authors": ["😀" * 128 for _ in range(32)],
        "abstract": "😀" * 16_000,
        "venue": "😀" * 512,
        "doi": "😀" * 512,
        "url": f"https://example.test/{'😀' * 1000}",
    }

    assert len(json.dumps(detail, ensure_ascii=False).encode()) <= 256 * 1024


def item(key: str, version: int, title: str, added: str) -> dict[str, Any]:
    return {
        "key": key,
        "version": version,
        "data": {
            "title": title,
            "date": "2026",
            "dateAdded": added,
            "collections": ["COLLECTION1"],
            "creators": [{"firstName": "Ada", "lastName": "Lovelace"}],
            "tags": [{"tag": "computing"}],
        },
    }


def attachment(key: str, parent: str) -> dict[str, Any]:
    return {
        "key": key,
        "version": 3,
        "data": {
            "contentType": "application/pdf",
            "linkMode": "imported_file",
            "parentItem": parent,
        },
    }
