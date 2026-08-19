from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from papers_bridge.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        zotero_user_id="12345",
        zotero_api_key=SecretStr("zotero-test-key"),
        reading_list_collection_keys=("COLLECTION1",),
        reading_list_bearer_token=SecretStr("b" * 32),
        docling_url="http://docling:5001",
        docling_api_key=SecretStr("d" * 32),
        cache_dir=tmp_path / "cache",
        cache_max_bytes=10 * 1024 * 1024,
        cache_ttl_seconds=3600,
        max_pdf_bytes=1024 * 1024,
    )
