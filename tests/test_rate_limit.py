from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.config import Settings
from src.rate_limit import FixedWindowLimiter
from src.server import create_api
from tests.conftest import make_settings

MISSING = "/api/v1/prescriptions/session-RX-NONE"


@pytest.fixture
def limited(settings: Settings) -> Iterator[Any]:
    clients: list[TestClient] = []

    def start(**overrides: Any) -> TestClient:
        client = TestClient(create_api(settings.model_copy(update=overrides)))
        client.__enter__()
        clients.append(client)
        return client

    yield start
    for client in clients:
        client.__exit__(None, None, None)


def test_fourth_request_in_a_minute_is_rejected(limited: Any) -> None:
    client = limited(RATE_LIMIT_PER_MINUTE=3)
    assert [client.get(MISSING).status_code for _ in range(3)] == [404, 404, 404]

    rejected = client.get(MISSING)
    assert rejected.status_code == 429
    assert rejected.json() == {"detail": "Too many requests. Try again in a minute."}
    assert 1 <= int(rejected.headers["Retry-After"]) <= 60


def test_each_forwarded_client_ip_has_its_own_budget(limited: Any) -> None:
    client = limited(RATE_LIMIT_PER_MINUTE=1, CLIENT_IP_HEADER="CF-Connecting-IP")
    first = {"CF-Connecting-IP": "203.0.113.1"}
    second = {"CF-Connecting-IP": "203.0.113.2"}
    assert [
        client.get(MISSING, headers=first).status_code,
        client.get(MISSING, headers=second).status_code,
        client.get(MISSING, headers=first).status_code,
        client.get(MISSING, headers=second).status_code,
    ] == [404, 404, 429, 429]


def test_health_is_never_limited(limited: Any) -> None:
    client = limited(RATE_LIMIT_PER_MINUTE=1)
    assert [client.get(path).status_code for path in ["/health", "/health/ready"] * 3] == [200] * 6
    assert [client.get(MISSING).status_code for _ in range(2)] == [404, 429]


def test_zero_turns_the_limit_off(limited: Any) -> None:
    client = limited(RATE_LIMIT_PER_MINUTE=0)
    assert [client.get(MISSING).status_code for _ in range(20)] == [404] * 20


def test_negative_limit_is_rejected() -> None:
    with pytest.raises(ValidationError, match="RATE_LIMIT_PER_MINUTE"):
        make_settings(LLM_PROVIDER="demo", ENVIRONMENT="test", RATE_LIMIT_PER_MINUTE=-1)


def test_budget_resets_when_the_window_rolls_over() -> None:
    now = [120.0]
    limiter = FixedWindowLimiter(2, clock=lambda: now[0])
    assert [limiter.hit("a"), limiter.hit("a"), limiter.hit("a"), limiter.hit("b")] == [
        None,
        None,
        60,
        None,
    ]
    now[0] = 179.5
    assert limiter.hit("a") == 1
    now[0] = 180.0
    assert [limiter.hit("a"), limiter.hit("a"), limiter.hit("a")] == [None, None, 60]
    now[0] = 190.0
    assert limiter.hit("a") == 50
