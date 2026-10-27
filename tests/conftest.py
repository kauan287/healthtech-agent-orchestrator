from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine

from src.config import Settings
from src.server import create_api


def make_settings(**overrides: Any) -> Settings:
    """Settings from `overrides` and defaults only, ignoring the environment and .env."""
    return Settings.model_validate(overrides)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(
        ENVIRONMENT="test",
        LLM_PROVIDER="demo",
        DATABASE_URL=f"sqlite:///{tmp_path / 'app.db'}",
        AUDIT_SIGNING_KEY="test-signing-key",
    )


@pytest.fixture
def restartable_client(settings: Settings) -> Iterator[Callable[[], TestClient]]:
    clients: list[TestClient] = []

    def start() -> TestClient:
        if clients:
            clients[-1].__exit__(None, None, None)
        client = TestClient(create_api(settings))
        client.__enter__()
        clients.append(client)
        return client

    yield start
    clients[-1].__exit__(None, None, None)


@pytest.fixture
def client(restartable_client: Callable[[], TestClient]) -> TestClient:
    return restartable_client()


@pytest.fixture
def raw_db(settings: Settings, client: TestClient) -> Engine:
    return create_engine(settings.DATABASE_URL)
