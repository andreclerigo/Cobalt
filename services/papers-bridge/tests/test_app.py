from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from papers_bridge.app import create_app
from papers_bridge.config import Settings


def test_health_is_public_but_data_routes_require_bearer_token(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/v1/health").json() == {"status": "ok"}
        response = client.get("/v1/collections")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_malformed_keys_are_rejected_before_upstream_access(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get(
            "/v1/collections/not%2Fa%2Fkey/snapshot",
            headers={"Authorization": f"Bearer {'b' * 32}"},
        )
    assert response.status_code in {404, 422}


def test_public_item_contract_is_item_qualified(settings: Settings) -> None:
    app = create_app(settings)
    paths = {route.path for route in app.routes}
    assert "/v1/items/{item_key}" in paths
    assert "/v1/items/{item_key}/conversion" in paths
    assert not any("{collection_key}/items" in path for path in paths)


def test_blank_or_short_secrets_fail_before_the_service_starts() -> None:
    with pytest.raises(ValidationError):
        Settings(
            zotero_user_id="123",
            zotero_api_key=SecretStr(" "),
            reading_list_collection_keys=("COLL1",),
            reading_list_bearer_token=SecretStr(""),
            docling_api_key=SecretStr("short"),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("zotero_base_url", "http://api.zotero.org"),
        ("zotero_base_url", "https://api.zotero.org.evil.test"),
        ("docling_url", "http://127.0.0.1:5001"),
        ("docling_url", "https://docling.example.test"),
    ],
)
def test_upstream_origins_are_fixed_to_the_deployment_boundary(
    settings: Settings, field: str, value: str
) -> None:
    values = {
        **settings.model_dump(),
        "zotero_api_key": settings.zotero_api_key,
        "reading_list_bearer_token": settings.reading_list_bearer_token,
        "docling_api_key": settings.docling_api_key,
        field: value,
    }
    with pytest.raises(ValidationError):
        Settings(**values)
