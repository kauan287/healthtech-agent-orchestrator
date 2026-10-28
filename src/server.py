"""FastAPI application for the HealthTech Agent Orchestrator.

The database is the system of record, and every clinical fact in a response is
read from the signed audit chain stored there. Endpoints are sync because the
LangGraph pipeline is called through its sync API. FastAPI runs them in a thread pool.
"""

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from starlette.types import ASGIApp, Receive, Scope, Send

from src.api_schemas import (
    AuditEntryResponse,
    AuditTrailResponse,
    CreatePrescriptionRequest,
    DoctorApprovalRequest,
    PrescriptionListResponse,
    PrescriptionResponse,
)
from src.audit import new_entry, seal_head
from src.clinical import build_clinical_model
from src.config import Settings, get_settings
from src.db import Base, PrescriptionRepository, StoredChain
from src.graph import Pipeline, build_pipeline
from src.prescription import DoctorDecision, Prescription, Status, Step, read_prescription
from src.rate_limit import FixedWindowLimiter

VERSION = "1.1.0"
THREAD_PREFIX = "session-"

logger = logging.getLogger(__name__)
router = APIRouter()


def create_api(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved = settings or get_settings()
        logging.basicConfig(level=resolved.LOG_LEVEL)
        engine = create_engine(resolved.DATABASE_URL, pool_pre_ping=True)
        Base.metadata.create_all(engine)
        key = resolved.AUDIT_SIGNING_KEY.encode()
        app.state.signing_key = key
        app.state.sessions = sessionmaker(engine, expire_on_commit=False)
        app.state.pipeline = build_pipeline(build_clinical_model(resolved), key)
        app.state.rate_limiter = (
            FixedWindowLimiter(resolved.RATE_LIMIT_PER_MINUTE)
            if resolved.RATE_LIMIT_PER_MINUTE > 0
            else None
        )
        app.state.client_ip_header = resolved.CLIENT_IP_HEADER
        app.state.cors_origins = resolved.cors_origins
        logger.info(
            "Ready: llm=%s, database=%s", resolved.LLM_PROVIDER, engine.url.get_backend_name()
        )
        yield
        engine.dispose()

    app = FastAPI(
        title="HealthTech Agent Orchestrator API",
        description=(
            "Multi-agent prescription review with LangGraph. A triage agent proposes a "
            "medication, a safety agent checks allergies, and a doctor approves or rejects. "
            "Every step is recorded in an HMAC-signed audit chain."
        ),
        version=VERSION,
        lifespan=lifespan,
    )
    app.include_router(router)
    app.add_exception_handler(RequestValidationError, _validation_error)
    # Registered unconditionally because settings are only resolved in the lifespan.
    app.middleware("http")(_rate_limit)
    # Added last so it runs first and a 429 from the rate limit still carries CORS headers.
    app.add_middleware(_CORSFromState)
    return app


class _CORSFromState:
    """CORSMiddleware with the origins the lifespan resolved, or a pass-through if there are none."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        origins: list[str] = scope["app"].state.cors_origins if scope["type"] == "http" else []
        if not origins:
            await self.app(scope, receive, send)
            return
        cors = CORSMiddleware(
            self.app,
            allow_origins=origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )
        await cors(scope, receive, send)


async def _rate_limit(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    limiter: FixedWindowLimiter | None = request.app.state.rate_limiter
    path = request.url.path
    if limiter is None or path == "/health" or path.startswith("/health/"):
        return await call_next(request)
    header: str = request.app.state.client_ip_header
    client = (header and request.headers.get(header)) or (
        request.client.host if request.client else "unknown"
    )
    retry_after = limiter.hit(client)
    if retry_after is not None:
        return JSONResponse(
            {"detail": "Too many requests. Try again in a minute."},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
    return await call_next(request)


async def _validation_error(request: Request, exc: Exception) -> Response:
    # The default handler echoes the input as UTF-8, which fails on a lone surrogate.
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    body = json.dumps({"detail": jsonable_encoder(errors)}, ensure_ascii=True)
    return Response(body, status_code=422, media_type="application/json")


def _repo(request: Request) -> Iterator[PrescriptionRepository]:
    session: Session
    with request.app.state.sessions() as session:
        yield PrescriptionRepository(session)


def _pipeline(request: Request) -> Pipeline:
    pipeline: Pipeline = request.app.state.pipeline
    return pipeline


def _signing_key(request: Request) -> bytes:
    key: bytes = request.app.state.signing_key
    return key


def _prescription_id(thread_id: str) -> str:
    if not thread_id.startswith(THREAD_PREFIX):
        raise HTTPException(404, "Prescription not found.")
    return thread_id.removeprefix(THREAD_PREFIX)


Repo = Annotated[PrescriptionRepository, Depends(_repo)]
SigningKey = Annotated[bytes, Depends(_signing_key)]


def _stored(repo: PrescriptionRepository, prescription_id: str) -> StoredChain:
    stored = repo.snapshot(prescription_id)
    if stored is None:
        raise HTTPException(404, "Prescription not found.")
    return stored


def _read(stored: StoredChain, prescription_id: str, key: bytes) -> Prescription:
    return read_prescription(
        stored.chain,
        key=key,
        prescription_id=prescription_id,
        head_hash=stored.head_hash,
        seal=stored.seal,
    )


def _response(prescription_id: str, rx: Prescription) -> PrescriptionResponse:
    return PrescriptionResponse(
        thread_id=THREAD_PREFIX + prescription_id,
        prescription_id=prescription_id,
        status=rx.status,
        proposed_medication=rx.triage.proposed_medication if rx.triage else None,
        dosage=rx.triage.dosage if rx.triage else None,
        safety_approved=rx.safety.is_safe if rx.safety else False,
        risk_notes=rx.safety.risk_notes if rx.safety else None,
        human_approved=rx.decision.approved if rx.decision else None,
        doctor_comments=rx.decision.comments if rx.decision else None,
        is_paused_waiting_approval=rx.status is Status.PENDING_APPROVAL,
        audit_chain_valid=rx.audit_valid,
        patient_symptoms=rx.triage.patient_symptoms if rx.triage else None,
        patient_allergies=rx.triage.patient_allergies if rx.triage else None,
        urgency_level=rx.triage.urgency_level if rx.triage else None,
        triage_reasoning=rx.triage.reasoning if rx.triage else None,
        created_at=rx.created_at,
    )


@router.get("/health", tags=["Ops"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": VERSION}


@router.get("/health/ready", tags=["Ops"])
def ready(repo: Repo) -> dict[str, str]:
    repo.session.execute(text("SELECT 1"))
    return {"status": "ready"}


@router.post(
    "/api/v1/prescriptions",
    response_model=PrescriptionResponse,
    status_code=201,
    tags=["Prescriptions"],
    summary="Run the triage and safety agents, then wait for the doctor",
    responses={409: {"description": "A prescription with this id already exists."}},
)
def create_prescription(
    req: CreatePrescriptionRequest,
    repo: Repo,
    pipeline: Annotated[Pipeline, Depends(_pipeline)],
    key: SigningKey,
) -> PrescriptionResponse:
    conflict = HTTPException(409, f"Prescription {req.prescription_id} already exists.")
    with repo.session.begin():
        if repo.head(req.prescription_id) is not None:
            raise conflict

    chain = pipeline.invoke(
        {
            "prescription_id": req.prescription_id,
            "patient_symptoms": req.patient_symptoms,
            "patient_allergies": req.patient_allergies,
        }
    )["audit_logs"]

    try:
        with repo.session.begin():
            seal = seal_head(key, req.prescription_id, chain[-1]["hash"])
            repo.add(req.prescription_id, chain, seal)
            stored = _stored(repo, req.prescription_id)
            return _response(req.prescription_id, _read(stored, req.prescription_id, key))
    except IntegrityError:
        if repo.head(req.prescription_id) is not None:
            raise conflict from None
        raise


@router.get(
    "/api/v1/prescriptions",
    response_model=PrescriptionListResponse,
    tags=["Prescriptions"],
    summary="List the newest prescriptions, each verified against its audit chain",
)
def list_prescriptions(
    repo: Repo, key: SigningKey, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> PrescriptionListResponse:
    items = []
    for prescription_id in repo.newest_ids(limit):
        stored = repo.snapshot(prescription_id)
        if stored is not None:
            items.append(_response(prescription_id, _read(stored, prescription_id, key)))
    return PrescriptionListResponse(items=items)


@router.get(
    "/api/v1/prescriptions/{thread_id}",
    response_model=PrescriptionResponse,
    tags=["Prescriptions"],
    summary="Read a prescription, verified against its audit chain",
)
def get_prescription(thread_id: str, repo: Repo, key: SigningKey) -> PrescriptionResponse:
    prescription_id = _prescription_id(thread_id)
    return _response(prescription_id, _read(_stored(repo, prescription_id), prescription_id, key))


@router.post(
    "/api/v1/prescriptions/{thread_id}/approve",
    response_model=PrescriptionResponse,
    tags=["Prescriptions"],
    summary="Record the doctor's decision",
    responses={
        409: {
            "description": "The prescription already has a decision, or its audit chain is invalid."
        }
    },
)
def decide_prescription(
    thread_id: str, req: DoctorApprovalRequest, repo: Repo, key: SigningKey
) -> PrescriptionResponse:
    prescription_id = _prescription_id(thread_id)
    with repo.session.begin():
        stored = _stored(repo, prescription_id)
        rx = _read(stored, prescription_id, key)
        if not rx.audit_valid:
            raise HTTPException(409, f"Audit chain is invalid: {rx.audit_detail}")
        if rx.status is not Status.PENDING_APPROVAL:
            raise HTTPException(409, f"Prescription is already {rx.status.value}.")

        decision = DoctorDecision(approved=req.approved, comments=req.doctor_comments)
        entry = new_entry(
            stored.chain,
            key=key,
            prescription_id=prescription_id,
            step=Step.DOCTOR.value,
            details=decision.model_dump(),
        )
        seal = seal_head(key, prescription_id, entry["hash"])
        if not repo.append(prescription_id, stored.head_hash, entry, seal):
            raise HTTPException(409, "Another decision was recorded first.")
        return _response(
            prescription_id, _read(_stored(repo, prescription_id), prescription_id, key)
        )


@router.get(
    "/api/v1/prescriptions/{thread_id}/audit",
    response_model=AuditTrailResponse,
    tags=["Prescriptions"],
    summary="Return the stored audit chain and the verification result",
)
def get_audit_trail(thread_id: str, repo: Repo, key: SigningKey) -> AuditTrailResponse:
    prescription_id = _prescription_id(thread_id)
    stored = _stored(repo, prescription_id)
    rx = _read(stored, prescription_id, key)
    return AuditTrailResponse(
        prescription_id=prescription_id,
        valid=rx.audit_valid,
        detail=rx.audit_detail,
        entries=[
            AuditEntryResponse(**entry).model_copy(
                update={} if rx.audit_valid else {"details": None}
            )
            for entry in stored.chain
        ],
    )


app = create_api()
