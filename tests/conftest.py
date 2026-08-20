"""
Shared pytest fixtures for the entire test suite.

LLM strategy
────────────
All tests patch src.agents.structured_triage_llm and
src.agents.structured_safety_llm to avoid hitting the real Groq API.

Database strategy
─────────────────
Integration tests override the FastAPI `get_db` dependency with an
in-memory SQLite session. Tables are created explicitly in the
test_client fixture because the FastAPI lifespan targets Postgres
(which is unavailable in CI) and gracefully skips the create_all call.
The SQLAlchemy JSON column type works transparently with SQLite.
"""

from unittest.mock import patch

import pytest
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Base
from src.schemas import SafetyCheck, TriageRecommendation

# ── Mock LLM responses ────────────────────────────────────────────────────────

TRIAGE_RESPONSE = TriageRecommendation(
    proposed_medication="Paracetamol",
    dosage="500mg cada 8 horas",
    reasoning="Fiebre leve sin contraindicaciones conocidas.",
    urgency_level="BAJO",
)

SAFETY_APPROVED = SafetyCheck(
    is_safe=True,
    risk_notes="Sin conflictos alérgicos detectados. Medicamento adecuado.",
)

SAFETY_REJECTED = SafetyCheck(
    is_safe=False,
    risk_notes="Alergia detectada: el principio activo puede causar reacción.",
)


@pytest.fixture
def mock_llm_approved():
    """Patch both LLM clients to return a safe, approved prescription."""
    with (
        patch("src.agents.structured_triage_llm") as mock_triage,
        patch("src.agents.structured_safety_llm") as mock_safety,
    ):
        mock_triage.invoke.return_value = TRIAGE_RESPONSE
        mock_safety.invoke.return_value = SAFETY_APPROVED
        yield mock_triage, mock_safety


@pytest.fixture
def mock_llm_rejected():
    """Patch both LLM clients so the safety agent flags an allergy conflict."""
    with (
        patch("src.agents.structured_triage_llm") as mock_triage,
        patch("src.agents.structured_safety_llm") as mock_safety,
    ):
        mock_triage.invoke.return_value = TRIAGE_RESPONSE
        mock_safety.invoke.return_value = SAFETY_REJECTED
        yield mock_triage, mock_safety


# ── In-memory SQLite DB ───────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def test_engine():
    """
    Single SQLite in-memory engine for the whole test session.

    StaticPool is required so every sessionmaker connection reuses the
    same underlying SQLite connection — without it each new connection
    gets a fresh, empty in-memory database and the tables are missing.

    Tables are created here, NOT via the lifespan (which targets Postgres
    and gracefully skips create_all in CI).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def db_session(test_engine) -> Session:
    """Provide a transactional DB session; roll back after each test."""
    TestSession = sessionmaker(bind=test_engine, autoflush=True, autocommit=False)
    session = TestSession()
    yield session
    session.rollback()
    session.close()


# ── FastAPI test client ───────────────────────────────────────────────────────


@pytest.fixture
def test_client(db_session, mock_llm_approved):
    """
    FastAPI TestClient with:
      - LLM calls mocked (no Groq API)
      - DB dependency overridden to use in-memory SQLite
    """
    from fastapi.testclient import TestClient

    from src.server import app, get_db

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
def test_client_rejected(db_session, mock_llm_rejected):
    """FastAPI TestClient where the safety agent rejects the prescription."""
    from fastapi.testclient import TestClient

    from src.server import app, get_db

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client

    app.dependency_overrides.clear()
