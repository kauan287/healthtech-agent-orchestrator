from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.config import Settings
from src.server import create_api

CONSOLE = "https://console.example.com"
PREFLIGHT = {
    "Access-Control-Request-Method": "POST",
    "Access-Control-Request-Headers": "content-type",
}


@pytest.fixture
def start(settings: Settings) -> Iterator[Any]:
    clients: list[TestClient] = []

    def start(**overrides: Any) -> TestClient:
        client = TestClient(create_api(settings.model_copy(update=overrides)))
        client.__enter__()
        clients.append(client)
        return client

    yield start
    for client in clients:
        client.__exit__(None, None, None)


def test_preflight_from_an_allowed_origin_is_allowed(start: Any) -> None:
    client = start(CORS_ORIGINS=f"https://other.example.com, {CONSOLE}")
    response = client.options("/api/v1/prescriptions", headers={"Origin": CONSOLE, **PREFLIGHT})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == CONSOLE
    assert response.headers["access-control-allow-methods"] == "GET, POST"


def test_preflight_from_another_origin_gets_no_cors_headers(start: Any) -> None:
    client = start(CORS_ORIGINS=CONSOLE)
    response = client.options(
        "/api/v1/prescriptions", headers={"Origin": "https://evil.example.com", **PREFLIGHT}
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
    allowed = client.get("/api/v1/prescriptions", headers={"Origin": CONSOLE})
    assert allowed.headers["access-control-allow-origin"] == CONSOLE


def test_empty_setting_turns_cors_off(start: Any) -> None:
    client = start(CORS_ORIGINS="")
    response = client.options("/api/v1/prescriptions", headers={"Origin": CONSOLE, **PREFLIGHT})
    assert (response.status_code, response.headers.get("access-control-allow-origin")) == (
        405,
        None,
    )


def test_a_rate_limited_response_carries_cors_headers(start: Any) -> None:
    client = start(CORS_ORIGINS=CONSOLE, RATE_LIMIT_PER_MINUTE=1)
    origin = {"Origin": CONSOLE}
    first, second = (client.get("/api/v1/prescriptions", headers=origin) for _ in range(2))
    assert (first.status_code, second.status_code) == (200, 429)
    assert second.headers["access-control-allow-origin"] == CONSOLE
