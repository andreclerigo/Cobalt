from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-owned configuration; secret values are never serialized."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    zotero_user_id: str
    zotero_api_key: SecretStr
    reading_list_collection_keys: tuple[str, ...]
    reading_list_bearer_token: SecretStr
    docling_url: str = "http://docling:5001"
    docling_api_key: SecretStr
    cache_dir: Path = Path("/var/lib/papers-bridge/cache")
    cache_max_bytes: int = 10 * 1024 * 1024 * 1024
    cache_ttl_seconds: int = 30 * 24 * 60 * 60
    max_pdf_bytes: int = 64 * 1024 * 1024
    conversion_timeout_seconds: int = 10 * 60
    conversion_queue_size: int = 8
    zotero_base_url: str = "https://api.zotero.org"

    @property
    def cache_configuration_fingerprint(self) -> str:
        """Invalidate derived content when its authorization boundary changes."""
        source = (
            "zotero-user:"
            + self.zotero_user_id
            + "\ncollections:"
            + ",".join(sorted(self.reading_list_collection_keys))
        ).encode("ascii")
        return hashlib.sha256(source).hexdigest()[:16]

    @field_validator("reading_list_collection_keys", mode="before")
    @classmethod
    def split_collection_keys(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator("reading_list_collection_keys")
    @classmethod
    def require_collection_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("READING_LIST_COLLECTION_KEYS must contain at least one key")
        if any(not key.isalnum() or len(key) > 32 for key in value):
            raise ValueError("collection keys must be short alphanumeric Zotero keys")
        return value

    @field_validator("zotero_user_id")
    @classmethod
    def valid_user_id(cls, value: str) -> str:
        if not value.isdigit():
            raise ValueError("ZOTERO_USER_ID must be numeric")
        return value

    @field_validator("zotero_api_key", "reading_list_bearer_token", "docling_api_key")
    @classmethod
    def require_secret(cls, value: SecretStr, info: ValidationInfo) -> SecretStr:
        secret = value.get_secret_value().strip()
        minimum = 1 if info.field_name == "zotero_api_key" else 32
        if len(secret) < minimum:
            raise ValueError(f"{info.field_name} must contain at least {minimum} characters")
        return SecretStr(secret)

    @field_validator("docling_url")
    @classmethod
    def require_internal_docling_origin(cls, value: str) -> str:
        if value.rstrip("/") != "http://docling:5001":
            raise ValueError("DOCLING_URL must be the internal Compose service origin")
        return "http://docling:5001"

    @field_validator("zotero_base_url")
    @classmethod
    def require_official_zotero_origin(cls, value: str) -> str:
        if value.rstrip("/") != "https://api.zotero.org":
            raise ValueError("ZOTERO_BASE_URL must be the official Zotero API origin")
        return "https://api.zotero.org"

    @field_validator(
        "cache_max_bytes",
        "cache_ttl_seconds",
        "max_pdf_bytes",
        "conversion_timeout_seconds",
        "conversion_queue_size",
    )
    @classmethod
    def positive_limit(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("resource limits must be positive")
        return value
