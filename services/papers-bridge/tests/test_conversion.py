from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import cast

import httpx
import pytest

import papers_bridge.conversion as conversion_module
from papers_bridge.cache import ConversionCache
from papers_bridge.config import Settings
from papers_bridge.conversion import (
    MAX_HTML_BYTES,
    ConversionManager,
    document_version,
    normalize_html,
)
from papers_bridge.models import CacheMetadata, ItemDetail
from papers_bridge.zotero import ZoteroClient


def test_normalizer_strips_active_content_and_extracts_figures() -> None:
    png = b"\x89PNG\r\n\x1a\nfigure"
    encoded = base64.b64encode(png).decode()
    source = (
        "<html><script>alert(1)</script><body><h1>Paper</h1>"
        f'<figure><img src="data:image/png;base64,{encoded}" alt="Plot">'
        "<figcaption>Result</figcaption></figure>"
        '<img src="https://tracker.example/pixel.png"><p onclick="bad()">Text</p></body></html>'
    )

    rendered, figures, truncated = normalize_html(source)

    assert "<script>" not in rendered
    assert "alert(1)" not in rendered
    assert "onclick" not in rendered
    assert "tracker.example" not in rendered
    assert 'src="figures/figure-001.png"' in rendered
    assert figures == {"figure-001.png": png}
    assert not truncated


def test_oversized_html_is_truncated_at_a_block_boundary() -> None:
    source = "".join(f"<p>{'x' * 4096}</p>" for _ in range(220))

    rendered, _, truncated = normalize_html(source)

    assert truncated
    assert len(rendered.encode()) <= MAX_HTML_BYTES
    assert rendered.endswith("</strong></p>")


def test_one_oversized_block_is_replaced_instead_of_cut_mid_block() -> None:
    rendered, _, truncated = normalize_html(f"<p>{'x' * (MAX_HTML_BYTES + 1)}</p>")

    assert truncated
    assert rendered == (
        "<p><strong>Converted text truncated at Cobalt's document limit.</strong></p>"
    )


def test_document_version_changes_with_attachment_version() -> None:
    assert document_version("ATTACH", 1) == document_version("ATTACH", 1)
    assert document_version("ATTACH", 1) != document_version("ATTACH", 2)


def test_document_version_changes_across_zotero_user_authorization_boundaries(
    settings: Settings,
) -> None:
    other = settings.model_copy(update={"zotero_user_id": "99999"})

    assert document_version(
        "ATTACH", 1, settings.cache_configuration_fingerprint
    ) != document_version("ATTACH", 1, other.cache_configuration_fingerprint)


@pytest.mark.asyncio
async def test_docling_upload_is_async_and_polled(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.headers["X-Api-Key"] == "d" * 32
        if request.url.path.endswith("/convert/file/async"):
            return httpx.Response(200, json={"task_id": "task-1", "task_status": "pending"})
        if request.url.path.endswith("/status/poll/task-1"):
            return httpx.Response(200, json={"task_status": "success"})
        if request.url.path.endswith("/result/task-1"):
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "document": {"html_content": "<h1>Converted</h1>"},
                },
            )
        raise AssertionError(request.url)

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(conversion_module.asyncio, "sleep", no_sleep)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = ConversionCache(tmp_path / "cache", max_bytes=1_000_000, ttl_seconds=60)
    manager = ConversionManager(settings, cast(ZoteroClient, object()), cache, client)

    assert await manager._docling(b"%PDF-1.7", "paper.pdf") == "<h1>Converted</h1>"
    assert calls == [
        ("POST", "/v1/convert/file/async"),
        ("GET", "/v1/status/poll/task-1"),
        ("GET", "/v1/result/task-1"),
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_duplicate_conversion_requests_share_one_job(
    settings: Settings, tmp_path: Path
) -> None:
    gate = asyncio.Event()

    class BlockingZotero:
        calls = 0

        async def download_pdf(self, _: object) -> bytes:
            self.calls += 1
            await gate.wait()
            return b"%PDF-1.7"

    zotero = BlockingZotero()
    cache = ConversionCache(tmp_path / "cache", max_bytes=1_000_000, ttl_seconds=60)
    cache.prepare()
    manager = ConversionManager(settings, cast(ZoteroClient, zotero), cache)
    detail = ItemDetail(
        key="ITEM1",
        version=1,
        title="Paper",
        creator_summary="Ada",
        year="2026",
        date_added="2026-01-01",
        tags=[],
        has_stored_pdf=True,
        authors=["Ada"],
        abstract="",
        venue="",
        doi="",
        url="",
        pdf_attachment_key="PDF1",
        pdf_attachment_version=1,
    )

    first = await manager.start(detail)
    job = manager._jobs["ITEM1"].task
    second = await manager.start(detail)

    assert first.state in {"queued", "running"}
    assert second.state in {"queued", "running"}
    assert manager._jobs["ITEM1"].task is job
    await manager.close()


@pytest.mark.asyncio
async def test_new_attachment_job_hides_an_older_cached_document(
    settings: Settings, tmp_path: Path
) -> None:
    gate = asyncio.Event()

    class BlockingZotero:
        async def download_pdf(self, _: object) -> bytes:
            await gate.wait()
            return b"%PDF-1.7"

    cache = ConversionCache(tmp_path / "cache", max_bytes=1_000_000, ttl_seconds=60)
    cache.prepare()
    old_version = document_version("PDF1", 1, settings.cache_configuration_fingerprint)
    cache.publish(
        CacheMetadata(
            item_key="ITEM1",
            attachment_key="PDF1",
            attachment_version=1,
            document_version=old_version,
            truncated=False,
            figures=[],
            bytes=0,
        ),
        b"<p>old</p>",
        {},
    )
    manager = ConversionManager(settings, cast(ZoteroClient, BlockingZotero()), cache)
    detail = ItemDetail(
        key="ITEM1",
        version=2,
        title="Paper",
        creator_summary="Ada",
        year="2026",
        date_added="2026-01-01",
        tags=[],
        has_stored_pdf=True,
        authors=["Ada"],
        abstract="",
        venue="",
        doi="",
        url="",
        pdf_attachment_key="PDF1",
        pdf_attachment_version=2,
    )

    started = await manager.start(detail)

    assert started.state in {"queued", "running"}
    assert manager.status("ITEM1").state in {"queued", "running"}
    assert manager.metadata("ITEM1") is None
    await manager.close()
