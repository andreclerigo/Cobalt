from __future__ import annotations

import hmac
import re
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request, status
from fastapi.responses import Response

from .cache import ConversionCache
from .config import Settings
from .conversion import ConversionManager, document_version
from .models import CollectionSummary, ConversionStatus, ItemDetail, Snapshot
from .zotero import InvalidUpstream, ZoteroClient, ZoteroError

KEY = re.compile(r"^[A-Za-z0-9]{1,32}$")
FIGURE = re.compile(r"^figure-[0-9]{3}\.(?:png|jpg)$")


@dataclass
class Resources:
    zotero: ZoteroClient
    cache: ConversionCache
    conversions: ConversionManager


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or Settings()  # type: ignore[call-arg]

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cache = ConversionCache(
            configured.cache_dir,
            max_bytes=configured.cache_max_bytes,
            ttl_seconds=configured.cache_ttl_seconds,
        )
        cache.prepare()
        zotero = ZoteroClient(configured)
        conversions = ConversionManager(configured, zotero, cache)
        app.state.resources = Resources(zotero=zotero, cache=cache, conversions=conversions)
        try:
            yield
        finally:
            await conversions.close()
            await zotero.close()

    app = FastAPI(
        title="Cobalt Papers Bridge",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    def authenticate(authorization: str | None = Header(default=None)) -> None:
        expected = f"Bearer {configured.reading_list_bearer_token.get_secret_value()}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def resources(request: Request) -> Resources:
        value: Resources = request.app.state.resources
        return value

    ResourceDependency = Annotated[Resources, Depends(resources)]
    KeyPath = Annotated[str, Path(pattern=KEY.pattern)]
    FigurePath = Annotated[str, Path(pattern=FIGURE.pattern)]
    LimitQuery = Annotated[int, Query(ge=1, le=500)]

    @app.get("/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/v1/collections",
        response_model=list[CollectionSummary],
        dependencies=[Depends(authenticate)],
    )
    async def collections(state: ResourceDependency) -> list[CollectionSummary]:
        return await _upstream(state.zotero.collections())

    @app.get(
        "/v1/collections/{collection_key}/snapshot",
        response_model=Snapshot,
        dependencies=[Depends(authenticate)],
    )
    async def snapshot(
        collection_key: KeyPath,
        state: ResourceDependency,
        limit: LimitQuery = 500,
    ) -> Snapshot:
        return await _upstream(state.zotero.snapshot(collection_key, limit))

    @app.get(
        "/v1/items/{item_key}",
        response_model=ItemDetail,
        dependencies=[Depends(authenticate)],
    )
    async def item(
        item_key: KeyPath,
        state: ResourceDependency,
    ) -> ItemDetail:
        return await _upstream(state.zotero.detail(item_key))

    @app.post(
        "/v1/items/{item_key}/conversion",
        response_model=ConversionStatus,
        dependencies=[Depends(authenticate)],
    )
    async def start_conversion(
        item_key: KeyPath,
        state: ResourceDependency,
    ) -> ConversionStatus:
        detail = await _upstream(state.zotero.detail(item_key))
        return await state.conversions.start(detail)

    @app.get(
        "/v1/items/{item_key}/conversion",
        response_model=ConversionStatus,
        dependencies=[Depends(authenticate)],
    )
    async def conversion(item_key: KeyPath, state: ResourceDependency) -> ConversionStatus:
        detail = await _upstream(state.zotero.detail(item_key))
        expected = _expected_version(configured, detail)
        if expected is None:
            return ConversionStatus(state="missing_pdf", message="No PDF is stored in Zotero.")
        return state.conversions.status(item_key, expected)

    @app.get("/v1/items/{item_key}/document", dependencies=[Depends(authenticate)])
    async def document(item_key: KeyPath, state: ResourceDependency) -> Response:
        detail = await _upstream(state.zotero.detail(item_key))
        expected = _expected_version(configured, detail)
        body = state.conversions.document(item_key, expected) if expected else None
        if body is None:
            raise HTTPException(status_code=404, detail="converted document not found")
        return Response(content=body, media_type="text/html; charset=utf-8")

    @app.get(
        "/v1/items/{item_key}/figures/{name}",
        dependencies=[Depends(authenticate)],
    )
    async def figure(
        item_key: KeyPath,
        name: FigurePath,
        state: ResourceDependency,
    ) -> Response:
        detail = await _upstream(state.zotero.detail(item_key))
        expected = _expected_version(configured, detail)
        body = state.conversions.figure(item_key, name, expected) if expected else None
        if body is None:
            raise HTTPException(status_code=404, detail="converted figure not found")
        media_type = "image/png" if name.endswith(".png") else "image/jpeg"
        return Response(content=body, media_type=media_type)

    return app


async def _upstream[T](awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except InvalidUpstream as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ZoteroError as error:
        raise HTTPException(status_code=502, detail="Zotero is temporarily unavailable") from error


def _expected_version(settings: Settings, detail: ItemDetail) -> str | None:
    if detail.pdf_attachment_key is None or detail.pdf_attachment_version is None:
        return None
    return document_version(
        detail.pdf_attachment_key,
        detail.pdf_attachment_version,
        settings.cache_configuration_fingerprint,
    )
