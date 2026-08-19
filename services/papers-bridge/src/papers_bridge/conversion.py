from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

import httpx

from .cache import ConversionCache
from .config import Settings
from .models import CacheMetadata, ConversionStatus, ItemDetail
from .zotero import PdfAttachment, ZoteroClient

MAX_HTML_BYTES = 768 * 1024
MAX_FIGURE_BYTES = 4 * 1024 * 1024
MAX_FIGURES = 64
MAX_DOCLING_CONTROL_BYTES = 64 * 1024
MAX_DOCLING_RESULT_BYTES = 128 * 1024 * 1024
CONVERTER_VERSION = "docling-html-v1"
BLOCK_TAGS = {"p", "div", "section", "article", "table", "ul", "ol", "pre", "blockquote"}
ALLOWED_TAGS = BLOCK_TAGS | {
    "html",
    "body",
    "main",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "strong",
    "b",
    "em",
    "i",
    "code",
    "br",
    "hr",
    "figure",
    "figcaption",
    "img",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "sup",
    "sub",
    "math",
    "mrow",
    "mi",
    "mn",
    "mo",
    "msup",
    "msub",
    "mfrac",
    "msqrt",
}
VOID_TAGS = {"br", "hr", "img"}
FIGURE_NAME = re.compile(r"^figure-[0-9]{3}\.(?:png|jpg)$")
ACTIVE_CONTENT_TAGS = {"script", "style", "iframe", "object", "embed", "svg"}


@dataclass
class Job:
    document_version: str
    status: ConversionStatus
    task: asyncio.Task[None]


class ConversionManager:
    def __init__(
        self,
        settings: Settings,
        zotero: ZoteroClient,
        cache: ConversionCache,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._zotero = zotero
        self._cache = cache
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        self._owns_client = client is None
        self._jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()
        self._lane = asyncio.Semaphore(1)

    async def close(self) -> None:
        jobs = list(self._jobs.values())
        for job in jobs:
            job.task.cancel()
        await asyncio.gather(*(job.task for job in jobs), return_exceptions=True)
        if self._owns_client:
            await self._client.aclose()

    async def start(self, detail: ItemDetail) -> ConversionStatus:
        if detail.pdf_attachment_key is None or detail.pdf_attachment_version is None:
            return ConversionStatus(state="missing_pdf", message="No PDF is stored in Zotero.")
        version = document_version(
            detail.pdf_attachment_key,
            detail.pdf_attachment_version,
            self._settings.cache_configuration_fingerprint,
        )
        cached = self._cache.find(detail.key, version)
        if cached is not None:
            return _ready(cached)
        async with self._lock:
            current = self._jobs.get(detail.key)
            if current is not None and not current.task.done():
                if current.document_version == version:
                    return current.status
                return ConversionStatus(
                    state="failed",
                    message="The stored PDF changed during conversion. Retry when it finishes.",
                )
            self._trim_jobs()
            active = sum(not queued.task.done() for queued in self._jobs.values())
            if active >= self._settings.conversion_queue_size:
                return ConversionStatus(
                    state="failed", message="The conversion queue is full. Retry later."
                )
            placeholder = asyncio.create_task(asyncio.sleep(0))
            job = Job(
                document_version=version,
                status=ConversionStatus(state="queued"),
                task=placeholder,
            )
            job.task = asyncio.create_task(self._run(detail, job, version))
            self._jobs[detail.key] = job
            return job.status

    def _trim_jobs(self) -> None:
        maximum = self._settings.conversion_queue_size * 2
        for key in list(self._jobs):
            if len(self._jobs) < maximum:
                break
            if self._jobs[key].task.done():
                self._jobs.pop(key)

    def status(self, item_key: str, expected_version: str | None = None) -> ConversionStatus:
        job = self._jobs.get(item_key)
        if job is not None and (not job.task.done() or job.status.state == "failed"):
            return job.status
        cached = self._cache.find(item_key, expected_version)
        return _ready(cached) if cached is not None else ConversionStatus(state="missing_pdf")

    def metadata(self, item_key: str) -> CacheMetadata | None:
        job = self._jobs.get(item_key)
        if job is not None and (not job.task.done() or job.status.state == "failed"):
            return None
        return self._cache.find(item_key)

    def document(self, item_key: str, expected_version: str) -> bytes | None:
        job = self._jobs.get(item_key)
        if job is not None and (not job.task.done() or job.status.state == "failed"):
            return None
        return self._cache.current_document(item_key, expected_version)

    def figure(self, item_key: str, name: str, expected_version: str) -> bytes | None:
        job = self._jobs.get(item_key)
        if job is not None and (not job.task.done() or job.status.state == "failed"):
            return None
        return self._cache.current_figure(item_key, name, expected_version)

    async def _run(self, detail: ItemDetail, job: Job, version: str) -> None:
        try:
            async with self._lane:
                job.status = ConversionStatus(state="running")
                attachment = PdfAttachment(
                    key=detail.pdf_attachment_key or "",
                    version=detail.pdf_attachment_version or 0,
                )
                pdf = await self._zotero.download_pdf(attachment)
                raw_html = await self._docling(pdf, f"{detail.key}.pdf")
                normalized, figures, truncated = normalize_html(raw_html)
                metadata = CacheMetadata(
                    item_key=detail.key,
                    attachment_key=attachment.key,
                    attachment_version=attachment.version,
                    document_version=version,
                    truncated=truncated,
                    figures=sorted(figures),
                    bytes=0,
                )
                published = await asyncio.to_thread(
                    self._cache.publish,
                    metadata,
                    normalized.encode("utf-8"),
                    figures,
                )
                job.status = _ready(published)
        except asyncio.CancelledError:
            raise
        except Exception:
            job.status = ConversionStatus(
                state="failed", message="The stored PDF could not be converted. Retry later."
            )

    async def _docling(self, pdf: bytes, filename: str) -> str:
        headers = {"X-Api-Key": self._settings.docling_api_key.get_secret_value()}
        async with asyncio.timeout(self._settings.conversion_timeout_seconds):
            submitted = await _bounded_json_request(
                self._client,
                "POST",
                f"{self._settings.docling_url}/v1/convert/file/async",
                maximum=MAX_DOCLING_CONTROL_BYTES,
                headers=headers,
                files={"files": (filename, pdf, "application/pdf")},
                data={
                    "from_formats": "pdf",
                    "to_formats": "html",
                    "do_ocr": "true",
                    "image_export_mode": "embedded",
                    "table_mode": "accurate",
                    "pipeline": "standard",
                },
            )
            if not isinstance(submitted, dict) or not isinstance(submitted.get("task_id"), str):
                raise ValueError("Docling returned no task identifier")
            task_id = submitted["task_id"]
            task_status = submitted.get("task_status")
            while task_status not in {"success", "failure"}:
                await asyncio.sleep(2)
                status_payload = await _bounded_json_request(
                    self._client,
                    "GET",
                    f"{self._settings.docling_url}/v1/status/poll/{task_id}",
                    maximum=MAX_DOCLING_CONTROL_BYTES,
                    headers=headers,
                )
                if not isinstance(status_payload, dict):
                    raise ValueError("Docling returned an invalid task status")
                task_status = status_payload.get("task_status")
                if task_status not in {"pending", "started", "success", "failure"}:
                    raise ValueError("Docling returned an unknown task status")
            if task_status == "failure":
                raise ValueError("Docling conversion failed")
            payload = await _bounded_json_request(
                self._client,
                "GET",
                f"{self._settings.docling_url}/v1/result/{task_id}",
                maximum=MAX_DOCLING_RESULT_BYTES,
                headers=headers,
            )
        if not isinstance(payload, dict) or payload.get("status") not in {
            "success",
            "partial_success",
        }:
            raise ValueError("Docling did not complete the conversion")
        document = payload.get("document")
        if not isinstance(document, dict):
            raise ValueError("Docling returned no HTML")
        html_content: object = document.get("html_content")
        if not isinstance(html_content, str):
            raise ValueError("Docling returned no HTML")
        return html_content


def document_version(
    attachment_key: str, attachment_version: int, configuration_fingerprint: str = ""
) -> str:
    source = (
        f"{attachment_key}:{attachment_version}:{CONVERTER_VERSION}:{configuration_fingerprint}"
    ).encode()
    return hashlib.sha256(source).hexdigest()[:24]


def normalize_html(source: str) -> tuple[str, dict[str, bytes], bool]:
    parser = Normalizer()
    parser.feed(source)
    parser.close()
    rendered = parser.render()
    encoded = rendered.encode("utf-8")
    if len(encoded) <= MAX_HTML_BYTES:
        return rendered, parser.figures, False
    notice = "<p><strong>Converted text truncated at Cobalt's document limit.</strong></p>"
    allowance = MAX_HTML_BYTES - len(notice.encode())
    cut = max((point for point in parser.checkpoints if point <= allowance), default=0)
    prefix = encoded[:cut].decode("utf-8", errors="ignore")
    return prefix + notice, parser.figures, True


class Normalizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.figures: dict[str, bytes] = {}
        self.checkpoints: list[int] = []
        self._bytes = 0
        self._suppressed_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in ACTIVE_CONTENT_TAGS:
            self._suppressed_depth += 1
            return
        if self._suppressed_depth:
            return
        if tag not in ALLOWED_TAGS:
            return
        if tag == "img":
            self._image(attrs)
            return
        attributes = ""
        if tag in {"th", "td"}:
            spans = []
            for name, value in attrs:
                if (
                    name in {"colspan", "rowspan"}
                    and value
                    and value.isdigit()
                    and int(value) <= 16
                ):
                    spans.append(f' {name}="{value}"')
            attributes = "".join(spans)
        self._append(f"<{tag}{attributes}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ACTIVE_CONTENT_TAGS and self._suppressed_depth:
            self._suppressed_depth -= 1
            return
        if self._suppressed_depth:
            return
        if tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        self._append(f"</{tag}>")
        if tag in BLOCK_TAGS:
            self.checkpoints.append(self._bytes)

    def handle_data(self, data: str) -> None:
        if data and not self._suppressed_depth:
            self._append(html.escape(data, quote=False))

    def render(self) -> str:
        return "".join(self.parts)

    def _image(self, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        source = values.get("src", "")
        alt = " ".join(values.get("alt", "").split())[:512]
        if len(self.figures) >= MAX_FIGURES or not source.startswith("data:image/"):
            if alt:
                self._append(f"<em>[Figure: {html.escape(alt)}]</em>")
            return
        header, separator, encoded = source.partition(",")
        if not separator or ";base64" not in header:
            return
        media_type = header[5:].split(";", 1)[0].lower()
        extension = {"image/png": "png", "image/jpeg": "jpg"}.get(media_type)
        if extension is None:
            return
        try:
            body = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            return
        if not body or len(body) > MAX_FIGURE_BYTES:
            return
        name = f"figure-{len(self.figures) + 1:03}.{extension}"
        if not FIGURE_NAME.fullmatch(name):
            return
        self.figures[name] = body
        self._append(f'<img src="figures/{name}" alt="{html.escape(alt, quote=True)}">')

    def _append(self, fragment: str) -> None:
        self.parts.append(fragment)
        self._bytes += len(fragment.encode("utf-8"))


def _ready(metadata: CacheMetadata) -> ConversionStatus:
    return ConversionStatus(
        state="ready",
        document_version=metadata.document_version,
        truncated=metadata.truncated,
    )


async def _bounded_json_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    maximum: int,
    **kwargs: Any,
) -> Any:
    body = bytearray()
    async with client.stream(method, url, **kwargs) as response:
        response.raise_for_status()
        length = response.headers.get("Content-Length", "")
        if length.isdigit() and int(length) > maximum:
            raise ValueError("Docling returned an oversized response")
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > maximum:
                raise ValueError("Docling returned an oversized response")
    try:
        return httpx.Response(200, content=bytes(body)).json()
    except ValueError as error:
        raise ValueError("Docling returned invalid JSON") from error
