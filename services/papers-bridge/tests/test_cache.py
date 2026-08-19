from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from papers_bridge.cache import ConversionCache
from papers_bridge.models import CacheMetadata


def metadata(version: str = "a" * 24) -> CacheMetadata:
    return CacheMetadata(
        item_key="ITEM1",
        attachment_key="ATTACH1",
        attachment_version=7,
        document_version=version,
        truncated=False,
        figures=["figure-001.png"],
        bytes=0,
    )


def test_publish_is_readable_and_rejects_unlisted_figures(tmp_path: Path) -> None:
    cache = ConversionCache(tmp_path, max_bytes=1_000_000, ttl_seconds=3600)
    cache.prepare()
    saved = cache.publish(metadata(), b"<p>paper</p>", {"figure-001.png": b"PNG"})

    assert cache.find("ITEM1") == saved
    assert cache.document(saved) == b"<p>paper</p>"
    assert cache.current_document("ITEM1", saved.document_version) == b"<p>paper</p>"
    assert cache.figure(saved, "figure-001.png") == b"PNG"
    assert cache.current_figure("ITEM1", "figure-001.png", saved.document_version) == b"PNG"
    assert cache.figure(saved, "../metadata.json") is None


def test_expired_versions_and_interrupted_publications_are_removed(tmp_path: Path) -> None:
    cache = ConversionCache(tmp_path, max_bytes=1_000_000, ttl_seconds=10)
    cache.prepare()
    saved = cache.publish(metadata(), b"<p>paper</p>", {"figure-001.png": b"PNG"})
    directory = tmp_path / saved.item_key / saved.document_version
    old = time.time() - 20
    os.utime(directory, (old, old))
    pending = tmp_path / ".pending-dead"
    pending.mkdir()

    cache.prepare()

    assert not pending.exists()
    assert cache.find("ITEM1") is None


def test_cache_rejects_path_components_even_if_a_caller_is_compromised(tmp_path: Path) -> None:
    cache = ConversionCache(tmp_path, max_bytes=1_000_000, ttl_seconds=3600)
    cache.prepare()
    unsafe = metadata().model_copy(update={"item_key": "../../outside"})

    with pytest.raises(ValueError, match="safe components"):
        cache.publish(unsafe, b"<p>paper</p>", {"figure-001.png": b"PNG"})

    assert not (tmp_path / "outside").exists()


def test_publish_fails_instead_of_claiming_ready_when_quota_evicts_it(
    tmp_path: Path,
) -> None:
    cache = ConversionCache(tmp_path, max_bytes=1, ttl_seconds=3600)
    cache.prepare()

    with pytest.raises(ValueError, match="cache capacity"):
        cache.publish(metadata(), b"<p>paper</p>", {"figure-001.png": b"PNG"})

    assert cache.find("ITEM1") is None
