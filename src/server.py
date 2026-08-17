"""
FastAPI application — entry point for the HealthTech Agent Orchestrator API.

Key design decisions:
  - Endpoints are synchronous (sync def) to avoid thread-pool complexity when
    wrapping LangGraph's synchronous .stream() / .update_state() APIs.
  - DB writes are non-blocking failures: if Postgres is unavailable the API
    still returns a correct response (degraded-but-functional mode).
  - The /health endpoint is dependency-free and always returns quickly.
"""
import logging
from contextlib import asynccontextmanager
from typing import Generator

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from src.api_schemas import (
    CreatePrescriptionRequest,
    DoctorApprovalRequest,
    PrescriptionResponse,
)
from src.audit import create_audit_entry, verify_audit_chain
from src.config import get_settings
from src.db.models import Base
from src.db.repository import AuditLogRepository, PrescriptionRepository
from src.db.session import engine, get_db_session
from src.graph import create_app
from src.state import PrescriptionState

logger = logging.getLogger(__name__)
settings = get_settings()

# ── LangGraph singleton ────────────────────────────────────────────────────────
# Created once at startup.  Each request is isolated by its unique thread_id.
orchestrator_app = create_app(interrupt_after=["safety_agent"])


# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Run DB migrations on startup; clean up on shutdown."""
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Database tables created/verified.")
    except Exception as exc:
        # Postgres may not be available in dev/test — degrade gracefully.
        logger.warning("⚠️  Could not connect to database: %s. Running without persistence.", exc)
    yield
    logger.info("Shutting down HealthTech API.")


# ── Application ────────────────────────────────────────────────────────────────
app = FastAPI(
    title="HealthTech Agent Orchestrator API",
    description=(
        "Production-grade backend for AI-driven medical prescription workflows. "
        "Features: LangGraph multi-agent pipeline, Human-in-the-Loop (HITL) interrupts, "
        "cryptographic SHA-256 audit chain, and PostgreSQL persistence."
    ),
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


# ── Dependency: DB session ─────────────────────────────────────────────────────
def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a transactional DB session.

    Override in tests via app.dependency_overrides[get_db] = ...
    """
    with get_db_session() as session:
        yield session


# ── Health ─────────────────────────────────────────────────────────────────────
@app.get("/health", tags=["Ops"])
def health_check():
    """Lightweight liveness probe — no DB or LLM calls."""
    return {
        "status": "ok",
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
    }


# ── Prescriptions ──────────────────────────────────────────────────────────────
@app.post(
    "/api/v1/prescriptions",
    response_model=PrescriptionResponse,
    status_code=200,
    tags=["Prescriptions"],
    summary="Start AI evaluation pipeline",
)
def start_prescription_flow(
    req: CreatePrescriptionRequest,
    db: Session = Depends(get_db),
):
    """
    Run the Triage + Safety agents, then pause for mandatory doctor approval.

    Returns immediately with `is_paused_waiting_approval: true`.
    Use POST /api/v1/prescriptions/{thread_id}/approve to resume.
    """
    thread_id = f"session-{req.prescription_id}"
    config = {"configurable": {"thread_id": thread_id}}

    initial_input: PrescriptionState = {
        "prescription_id": req.prescription_id,
        "patient_symptoms": req.patient_symptoms,
        "patient_allergies": req.patient_allergies,
        "proposed_medication": None,
        "dosage": None,
        "safety_approved": False,
        "risk_notes": None,
        "human_approved": None,
        "doctor_comments": None,
        "audit_logs": [],
    }

    # Run agents until the HITL interrupt fires
    for _ in orchestrator_app.stream(initial_input, config):
        pass

    state_values = orchestrator_app.get_state(config).values
    audit_valid, _ = verify_audit_chain(state_values.get("audit_logs", []))

    # ── Persist to DB (non-blocking failure) ──────────────────────────────────
    try:
        prescription_repo = PrescriptionRepository(db)
        audit_repo = AuditLogRepository(db)

        prescription_repo.create(
            prescription_id=req.prescription_id,
            thread_id=thread_id,
            patient_symptoms=req.patient_symptoms,
            patient_allergies=req.patient_allergies,
        )
        prescription_repo.update_after_agents(
            prescription_id=req.prescription_id,
            proposed_medication=state_values.get("proposed_medication") or "",
            dosage=state_values.get("dosage") or "",
            safety_approved=state_values.get("safety_approved", False),
            risk_notes=state_values.get("risk_notes") or "",
        )
        audit_repo.bulk_create(req.prescription_id, state_values.get("audit_logs", []))
        logger.info("Prescription %s persisted to DB.", req.prescription_id)
    except Exception as exc:
        logger.warning("DB persistence failed (non-blocking): %s", exc)

    return PrescriptionResponse(
        thread_id=thread_id,
        prescription_id=state_values["prescription_id"],
        proposed_medication=state_values.get("proposed_medication"),
        dosage=state_values.get("dosage"),
        safety_approved=state_values.get("safety_approved", False),
        risk_notes=state_values.get("risk_notes"),
        human_approved=state_values.get("human_approved"),
        doctor_comments=state_values.get("doctor_comments"),
        is_paused_waiting_approval=True,
        audit_chain_valid=audit_valid,
    )


@app.get(
    "/api/v1/prescriptions/{thread_id}",
    response_model=PrescriptionResponse,
    tags=["Prescriptions"],
    summary="Get prescription session status",
)
def get_prescription_status(thread_id: str):
    """
    Retrieve the current LangGraph state for a session.

    `is_paused_waiting_approval` is True while awaiting doctor sign-off.
    """
    config = {"configurable": {"thread_id": thread_id}}
    state = orchestrator_app.get_state(config)

    if not state.values:
        raise HTTPException(status_code=404, detail="Session not found.")

    state_values = state.values
    audit_valid, _ = verify_audit_chain(state_values.get("audit_logs", []))
    # Semantic HITL check: the graph is awaiting a doctor decision when
    # human_approved has not yet been set. This is robust across LangGraph
    # versions, which differ in how they signal interrupts via state.next/tasks.
    is_paused = state_values.get("human_approved") is None

    return PrescriptionResponse(
        thread_id=thread_id,
        prescription_id=state_values["prescription_id"],
        proposed_medication=state_values.get("proposed_medication"),
        dosage=state_values.get("dosage"),
        safety_approved=state_values.get("safety_approved", False),
        risk_notes=state_values.get("risk_notes"),
        human_approved=state_values.get("human_approved"),
        doctor_comments=state_values.get("doctor_comments"),
        is_paused_waiting_approval=is_paused,
        audit_chain_valid=audit_valid,
    )


@app.post(
    "/api/v1/prescriptions/{thread_id}/approve",
    response_model=PrescriptionResponse,
    tags=["Prescriptions"],
    summary="Submit doctor approval decision",
)
def approve_prescription(
    thread_id: str,
    req: DoctorApprovalRequest,
    db: Session = Depends(get_db),
):
    """
    Record the doctor's decision, append the final audit log entry,
    and resume the LangGraph flow to completion.
    """
    config = {"configurable": {"thread_id": thread_id}}
    current_state = orchestrator_app.get_state(config)

    if not current_state.values:
        raise HTTPException(status_code=404, detail="Session not found.")

    prescription_id: str = current_state.values["prescription_id"]
    existing_logs = current_state.values.get("audit_logs", [])
    last_log = existing_logs[-1] if existing_logs else None

    human_log = create_audit_entry(
        previous_log=last_log,
        step="HUMAN_APPROVAL",
        action="Doctor Decision Recorded via API",
        details={"approved": req.approved, "comments": req.doctor_comments},
    )

    orchestrator_app.update_state(
        config,
        {
            "human_approved": req.approved,
            "doctor_comments": req.doctor_comments,
            "audit_logs": [human_log],
        },
        as_node="safety_agent",
    )

    for _ in orchestrator_app.stream(None, config):
        pass

    final_values = orchestrator_app.get_state(config).values
    audit_valid, _ = verify_audit_chain(final_values.get("audit_logs", []))

    # ── Persist to DB (non-blocking failure) ──────────────────────────────────
    try:
        prescription_repo = PrescriptionRepository(db)
        audit_repo = AuditLogRepository(db)

        prescription_repo.finalize(
            prescription_id=prescription_id,
            human_approved=req.approved,
            doctor_comments=req.doctor_comments,
        )
        audit_repo.bulk_create(prescription_id, [human_log])
        logger.info("Prescription %s finalized in DB (approved=%s).", prescription_id, req.approved)
    except Exception as exc:
        logger.warning("DB persistence failed (non-blocking): %s", exc)

    return PrescriptionResponse(
        thread_id=thread_id,
        prescription_id=final_values["prescription_id"],
        proposed_medication=final_values.get("proposed_medication"),
        dosage=final_values.get("dosage"),
        safety_approved=final_values.get("safety_approved", False),
        risk_notes=final_values.get("risk_notes"),
        human_approved=final_values.get("human_approved"),
        doctor_comments=final_values.get("doctor_comments"),
        is_paused_waiting_approval=False,
        audit_chain_valid=audit_valid,
    )