from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CollectionSummary(StrictModel):
    key: str
    name: str


class ItemSummary(StrictModel):
    key: str
    version: int
    title: str
    creator_summary: str
    year: str
    date_added: str
    tags: list[str] = Field(max_length=12)
    has_stored_pdf: bool


class Snapshot(StrictModel):
    collection_key: str
    revision: str
    total: int
    truncated: bool
    items: list[ItemSummary]


class ItemDetail(ItemSummary):
    authors: list[str] = Field(max_length=32)
    abstract: str
    venue: str
    doi: str
    url: str
    pdf_attachment_key: str | None = Field(default=None, exclude=True)
    pdf_attachment_version: int | None = Field(default=None, exclude=True)


ConversionState = Literal["missing_pdf", "queued", "running", "ready", "failed"]


class ConversionStatus(StrictModel):
    state: ConversionState
    document_version: str | None = None
    truncated: bool = False
    message: str | None = None


class CacheMetadata(StrictModel):
    item_key: str
    attachment_key: str
    attachment_version: int
    document_version: str
    truncated: bool
    figures: list[str]
    bytes: int
