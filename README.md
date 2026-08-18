# 🏥 HealthTech Agent Orchestrator

[![CI](https://github.com/kauan287/healthtech-agent-orchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/kauan287/healthtech-agent-orchestrator/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12+-3776AB.svg?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.140+-009688.svg?logo=fastapi)](https://fastapi.tiangolo.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.2+-FF6B35.svg)](https://langchain-ai.github.io/langgraph/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791.svg?logo=postgresql&logoColor=white)](https://postgresql.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **Production-grade AI Agent Orchestration backend** for medical prescription workflows.
> Built with LangGraph, FastAPI, and PostgreSQL — featuring a cryptographic SHA-256 audit chain,
> Human-in-the-Loop (HITL) interrupts, and full Docker Compose deployment.

---

## 🔗 Part of the MedTech Ecosystem

This project is **Project 1** of a two-project medtech portfolio. It generates and doctor-approves
prescriptions; [SmartDispenser Core](https://github.com/kauan287/smartdispenser-core) consumes them
to physically release medication from smart vending machines.

```
HealthTech Agent Orchestrator (this repo)       SmartDispenser Core (Project 2)
  Python · FastAPI · LangGraph · PostgreSQL   →   C# · ASP.NET Core 9 · SignalR · EF Core

  Triage Agent evaluates prescription              Verifies approval + audit chain
  Safety Agent checks allergies                    Validates payment (HMAC webhook)
  Doctor approves via HITL                         Commands physical machine via SignalR
  SHA-256 audit chain signed                       Blazor real-time dashboard
```

📦 **[SmartDispenser Core →](https://github.com/kauan287/smartdispenser-core)**

---

## 🏗️ Architecture

The system runs a two-stage LangGraph multi-agent pipeline. Each step is recorded in a **blockchain-style hash chain** — making any retroactive data manipulation detectable at the application level.

```
POST /api/v1/prescriptions
         │
         ▼
 ┌───────────────┐     SHA-256 log      ┌──────────────────┐
 │  Triage Agent │ ──────────────────►  │  Cryptographic   │
 │  (LLM: Groq)  │  proposes medication │  Audit Chain     │
 └───────┬───────┘                      │                  │
         │                              │  entry[0].hash   │
         ▼                              │       ↕           │
 ┌───────────────┐     SHA-256 log      │  entry[1].hash   │
 │ Safety Agent  │ ──────────────────►  │       ↕           │
 │  (LLM: Groq)  │  validates allergies │  entry[2].hash   │
 └───────┬───────┘                      └──────────────────┘
         │
         ▼  ← HITL INTERRUPT (graph pauses here)
         │
POST /api/v1/prescriptions/{id}/approve
         │
         ▼
 ┌───────────────┐
 │ Doctor signs  │
 │ off decision  │
 └───────┬───────┘
         │
         ▼
 ┌───────────────────────────────────┐
 │  PostgreSQL  (Docker Compose)     │
 │  • prescriptions table            │
 │  • audit_logs table (immutable)   │
 └───────────────────────────────────┘
```

---

## ✨ Key Features

| Feature | Details |
|---|---|
| **Multi-Agent Pipeline** | LangGraph `StateGraph` with Triage + Safety agents and structured Pydantic outputs |
| **Human-in-the-Loop** | Graph `interrupt_after` pattern — mandatory doctor sign-off before finalization |
| **Cryptographic Audit Chain** | SHA-256 chained logs, blockchain-inspired; tamper detection even if DB is compromised |
| **PostgreSQL Persistence** | SQLAlchemy 2.x ORM; `prescriptions` + `audit_logs` tables; Repository pattern |
| **Docker Compose** | One-command local setup: `docker compose up --build` |
| **CI/CD Pipeline** | GitHub Actions: lint (ruff + mypy) → pytest with coverage → Docker smoke test |
| **Test Suite** | Unit tests (mocked LLM) + Integration tests (FastAPI TestClient + SQLite) |
| **Graceful Degradation** | DB unavailable? API returns correct responses; writes fail silently with a warning |

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Agent Orchestration | [LangGraph](https://langchain-ai.github.io/langgraph/) 1.2+ |
| LLM Provider | [Groq](https://groq.com/) (`llama-3.3-70b-versatile`) |
| API Framework | [FastAPI](https://fastapi.tiangolo.com/) 0.140+ |
| Database ORM | [SQLAlchemy](https://sqlalchemy.org/) 2.0 |
| Database | PostgreSQL 16 (psycopg2) |
| Package Manager | [uv](https://docs.astral.sh/uv/) |
| Linter / Formatter | [ruff](https://docs.astral.sh/ruff/) |
| Type Checker | mypy |
| Test Framework | pytest + httpx |
| Containerization | Docker + Docker Compose |
| CI/CD | GitHub Actions |

---

## 🚀 Quick Start

### Option 1: Docker Compose *(recommended)*

```bash
git clone https://github.com/kauan287/healthtech-agent-orchestrator.git
cd healthtech-agent-orchestrator

cp .env.example .env
# Edit .env — add your GROQ_API_KEY (free at https://console.groq.com/keys)

docker compose up --build
```

- **API:** http://localhost:8000
- **Interactive Docs:** http://localhost:8000/docs
- **PostgreSQL:** `localhost:5432` (user: `healthtech`, db: `healthtech_db`)

### Option 2: Local Development (uv)

```bash
git clone https://github.com/kauan287/healthtech-agent-orchestrator.git
cd healthtech-agent-orchestrator

# Install all deps including dev tooling
uv sync --all-groups

cp .env.example .env
# Edit .env — add GROQ_API_KEY (PostgreSQL is optional; API degrades gracefully)

uv run uvicorn src.server:app --reload
```

---

## 📡 API Reference

Full interactive docs available at `/docs` when the server is running.

### `POST /api/v1/prescriptions` — Start AI evaluation

```bash
curl -X POST http://localhost:8000/api/v1/prescriptions \
  -H "Content-Type: application/json" \
  -d '{
    "prescription_id": "RX-2024-001",
    "patient_symptoms": "Fiebre alta de 39°C y dolor articular",
    "patient_allergies": ["Ibuprofeno"]
  }'
```

Response — graph is paused awaiting doctor approval:
```json
{
  "thread_id": "session-RX-2024-001",
  "prescription_id": "RX-2024-001",
  "proposed_medication": "Paracetamol",
  "dosage": "500mg cada 8 horas",
  "safety_approved": true,
  "risk_notes": "Sin conflictos con alergias conocidas.",
  "human_approved": null,
  "is_paused_waiting_approval": true,
  "audit_chain_valid": true
}
```

### `GET /api/v1/prescriptions/{thread_id}` — Get session status

```bash
curl http://localhost:8000/api/v1/prescriptions/session-RX-2024-001
```

### `POST /api/v1/prescriptions/{thread_id}/approve` — Doctor decision

```bash
curl -X POST http://localhost:8000/api/v1/prescriptions/session-RX-2024-001/approve \
  -H "Content-Type: application/json" \
  -d '{
    "approved": true,
    "doctor_comments": "Paciente alérgico al ibuprofeno confirmado. Paracetamol adecuado."
  }'
```

### `GET /health` — Liveness probe

```bash
curl http://localhost:8000/health
# {"status": "ok", "version": "1.0.0", "environment": "development"}
```

---

## 🧪 Testing

```bash
# Install dev dependencies
uv sync --all-groups

# Run full suite with coverage report
uv run pytest tests/ -v --cov=src --cov-report=term-missing

# Run only unit tests (fast — no network, no DB)
uv run pytest tests/unit/ -v

# Run only integration tests
uv run pytest tests/integration/ -v

# Lint
uv run ruff check src/ tests/

# Format
uv run ruff format src/ tests/

# Type check
uv run mypy src/ --ignore-missing-imports
```

> **No infrastructure needed for tests.** LLM calls are mocked via `unittest.mock.patch`.
> The DB dependency is overridden with an in-memory SQLite session — no Postgres required.

---

## 📁 Project Structure

```
healthtech-agent-orchestrator/
├── src/
│   ├── agents.py           # Triage + Safety LangGraph nodes
│   ├── api_schemas.py      # FastAPI request/response models
│   ├── audit.py            # SHA-256 cryptographic audit chain
│   ├── config.py           # Pydantic Settings (env-aware, cached)
│   ├── graph.py            # LangGraph StateGraph definition
│   ├── schemas.py          # LLM structured output schemas (Pydantic)
│   ├── server.py           # FastAPI application + dependency injection
│   ├── state.py            # LangGraph TypedDict state definition
│   └── db/
│       ├── models.py       # SQLAlchemy 2.x ORM models
│       ├── repository.py   # Repository pattern (data access layer)
│       └── session.py      # DB engine + session factory
├── tests/
│   ├── conftest.py         # Shared fixtures (mock LLM, SQLite override)
│   ├── unit/
│   │   ├── test_audit.py   # Audit chain: hash chaining, tamper detection
│   │   ├── test_graph.py   # LangGraph: node wiring, interrupt, resume
│   │   └── test_schemas.py # Pydantic schema validation
│   └── integration/
│       └── test_api.py     # Full HTTP lifecycle (TestClient + SQLite)
├── .github/
│   └── workflows/
│       └── ci.yml          # CI: lint → test (with coverage) → docker build
├── docker-compose.yml      # PostgreSQL 16 + API service
├── Dockerfile              # Multi-stage build (Python 3.12-slim)
├── Makefile                # Developer convenience targets
├── pyproject.toml          # Project metadata + ruff/mypy/pytest config
└── README.md
```

---

## ⚖️ Trade-offs & Technical Decisions

### 1. Blockchain-style Hash Chain vs. Append-only DB Table

**Decision:** Every audit log entry contains a SHA-256 hash computed from its own content *plus* the previous entry's hash — forming a tamper-evident chain.

**Why:** Standard append-only tables with DB-level restrictions (`INSERT ONLY`) prevent easy deletions but cannot detect **retroactive field-level modifications** — e.g., a DBA changing `proposed_medication` from "Paracetamol" to "Morfina" directly in the database. The hash chain makes this detectable at the application level on the next `verify_audit_chain()` call, even years later.

**Trade-off:** Hash computation is sequential (each entry depends on the previous) and cannot be parallelized. For high-throughput systems this is a bottleneck. The production-grade alternative would be a dedicated immutable ledger service (e.g., AWS QLDB or Azure Confidential Ledger) that provides hardware-attested write-once guarantees without the sequential constraint.

---

### 2. LangGraph `MemorySaver` vs. `AsyncpgSaver` for HITL State

**Decision:** The LangGraph graph uses `MemorySaver` (in-process dict) to hold session state between the HITL interrupt and the doctor's approval.

**Why:** `MemorySaver` is zero-dependency, works in any environment, and makes unit testing trivial (no DB or network). For a single-instance deployment it is functionally correct.

**Trade-off:** State is **lost on server restart**, and **horizontal scaling is impossible** — each API instance has its own memory. `langgraph-checkpoint-postgres` (`AsyncpgSaver`) solves both: state is stored in the same PostgreSQL instance already provided by `docker-compose.yml`, and any replica can resume any session. This is the planned production upgrade; the migration requires changing one line in `graph.py`.

**Why not now:** `AsyncpgSaver` requires async SQLAlchemy throughout the entire request chain, which conflicts with LangGraph's current synchronous `.stream()` API. The migration is non-trivial and would obscure the core orchestration logic for a portfolio demo.

---

### 3. Synchronous FastAPI Endpoints

**Decision:** All endpoints are `def` (sync), not `async def`, despite FastAPI's native async support.

**Why:** LangGraph's `.stream()` and `.update_state()` are synchronous APIs. Wrapping them inside `async def` endpoints would require `asyncio.to_thread()` (or `run_in_executor`), adding complexity and potential deadlock risk with no concurrency benefit at single-instance scale.

**Trade-off:** Each synchronous endpoint blocks a Uvicorn worker thread for the duration of LLM inference (typically 2–8 seconds with Groq). With `uvicorn --workers N` this scales linearly with N. For production-grade concurrency under real load: migrate to `async def` + `asyncio.to_thread(orchestrator_app.stream, ...)`.

---

### 4. HITL as Graph Interrupt vs. Webhook / Callback Pattern

**Decision:** Doctor approval uses LangGraph's `interrupt_after` mechanism — the graph suspends mid-execution and resumes when `update_state` is called.

**Why:** All workflow state stays inside the graph; transitions are explicit and visible in the state object. No external event bus, message queue, or callback URL management is required. The API surface is simple: one endpoint to create, one to approve.

**Trade-off:** The "paused" state lives in `MemorySaver`. A server restart between prescription creation and doctor approval **loses the session** (mitigated by migrating to `AsyncpgSaver` as above). A webhook-based approach — return an `approval_token` immediately, persist to DB, notify doctor via email/push, accept callback on a separate route — is more resilient for long waits (hours, days) but significantly more complex. For the clinical use case, the webhook approach is likely the right long-term design.

---

### 5. Single LLM for Both Agents vs. Specialized Models per Role

**Decision:** Both the Triage Agent and the Safety Agent use the same `llama-3.3-70b-versatile` model on Groq's free tier.

**Why:** The primary differentiator of this system is the **orchestration pattern** (multi-agent graph, HITL, cryptographic audit), not the model selection. Using Pydantic structured outputs (`with_structured_output`) adds a schema-level safety net against hallucinated formats regardless of which model is used.

**Trade-off:** A medically fine-tuned model (trained on clinical decision support datasets, e.g., [MedPaLM](https://sites.research.google/med-palm/)) would produce significantly more accurate triage proposals. The architecture is deliberately model-agnostic — swapping `ChatGroq` for any `langchain_core.language_models.BaseChatModel` requires changing **one line** in `agents.py`.

---

## 📄 License

MIT — see [LICENSE](LICENSE) for details.
