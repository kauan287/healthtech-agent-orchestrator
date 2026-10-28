import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, make_url, text

from src.config import Settings
from src.server import create_api


def make_settings(**overrides: Any) -> Settings:
    """Settings from `overrides` and defaults only, ignoring the environment and .env."""
    return Settings.model_validate(overrides)


@pytest.fixture
def database_url(tmp_path: Path) -> Iterator[str]:
    """A fresh SQLite file, or a fresh PostgreSQL database when TEST_POSTGRES_URL is set."""
    server_url = os.environ.get("TEST_POSTGRES_URL")
    if not server_url:
        yield f"sqlite:///{tmp_path / 'app.db'}"
        return
    name = f"test_{uuid.uuid4().hex[:12]}"
    server = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    yield make_url(server_url).set(database=name).render_as_string(hide_password=False)
    with server.connect() as conn:
        conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    server.dispose()


@pytest.fixture
def settings(database_url: str) -> Settings:
    return make_settings(
        ENVIRONMENT="test",
        LLM_PROVIDER="demo",
        DATABASE_URL=database_url,
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
