"""
Integration tests for the FastAPI REST layer.

Strategy
────────
- LLM: patched via conftest fixtures (no real Groq API calls)
- Database: FastAPI get_db dependency overridden to use in-memory SQLite
- LangGraph: uses a real MemorySaver graph with mocked agent nodes

Each test that creates a prescription uses a unique prescription_id
(via uuid) to avoid state collisions in the shared MemorySaver instance.

Tests cover:
  1. Health endpoint
  2. Happy-path full workflow (create → status → approve)
  3. Allergy rejection (safety_approved=False)
  4. 404 for unknown session
  5. DB persistence verification
"""

import uuid

from sqlalchemy.orm import Session

from src.db.models import AuditLogRecord, PrescriptionRecord


def unique_pid() -> str:
    """Generate a unique prescription ID for each test run."""
    return f"RX-{uuid.uuid4().hex[:8].upper()}"


# ── Health ────────────────────────────────────────────────────────────────────


class TestHealth:
    def test_health_returns_200(self, test_client):
        resp = test_client.get("/health")
        assert resp.status_code == 200

    def test_health_response_shape(self, test_client):
        data = test_client.get("/health").json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "environment" in data


# ── Happy-path workflow ───────────────────────────────────────────────────────


class TestPrescriptionHappyPath:
    """Full HITL workflow: create → check status → approve."""

    def test_create_prescription_returns_200(self, test_client):
        pid = unique_pid()
        resp = test_client.post(
            "/api/v1/prescriptions",
            json={
                "prescription_id": pid,
                "patient_symptoms": "Fiebre alta de 39°C y dolor articular",
                "patient_allergies": [],
            },
        )
        assert resp.status_code == 200

    def test_create_prescription_is_paused(self, test_client):
        pid = unique_pid()
        data = test_client.post(
            "/api/v1/prescriptions",
            json={
                "prescription_id": pid,
                "patient_symptoms": "Dolor de cabeza",
                "patient_allergies": [],
            },
        ).json()
        assert data["is_paused_waiting_approval"] is True

    def test_create_prescription_has_valid_audit_chain(self, test_client):
        pid = unique_pid()
        data = test_client.post(
            "/api/v1/prescriptions",
            json={
                "prescription_id": pid,
                "patient_symptoms": "Tos seca",
                "patient_allergies": [],
            },
        ).json()
        assert data["audit_chain_valid"] is True

    def test_full_workflow_end_to_end(self, test_client):
        """
        Runs the complete prescription lifecycle:
          1. POST /prescriptions       — agents evaluate, HITL pause
          2. GET  /prescriptions/{id}  — verify paused state
          3. POST /prescriptions/{id}/approve — doctor approves
          4. Verify final state
        """
        pid = unique_pid()

        # Step 1: Create
        create_resp = test_client.post(
            "/api/v1/prescriptions",
            json={
                "prescription_id": pid,
                "patient_symptoms": "Fiebre 38°C",
                "patient_allergies": ["Aspirina"],
            },
        )
        assert create_resp.status_code == 200
        create_data = create_resp.json()
        thread_id = create_data["thread_id"]

        assert create_data["proposed_medication"] == "Paracetamol"
        assert create_data["safety_approved"] is True
        assert create_data["is_paused_waiting_approval"] is True

        # Step 2: Get status
        status_resp = test_client.get(f"/api/v1/prescriptions/{thread_id}")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["is_paused_waiting_approval"] is True
        assert status_data["prescription_id"] == pid

        # Step 3: Doctor approves
        approve_resp = test_client.post(
            f"/api/v1/prescriptions/{thread_id}/approve",
            json={
                "approved": True,
                "doctor_comments": "Correcto, sin contraindicaciones.",
            },
        )
        assert approve_resp.status_code == 200
        approve_data = approve_resp.json()

        assert approve_data["human_approved"] is True
        assert approve_data["is_paused_waiting_approval"] is False
        assert approve_data["audit_chain_valid"] is True
        assert approve_data["doctor_comments"] == "Correcto, sin contraindicaciones."

    def test_doctor_rejection_workflow(self, test_client):
        """Doctor rejects the prescription — human_approved should be False."""
        pid = unique_pid()

        create_data = test_client.post(
            "/api/v1/prescriptions",
            json={
                "prescription_id": pid,
                "patient_symptoms": "Dolor abdominal",
                "patient_allergies": [],
            },
        ).json()
        thread_id = create_data["thread_id"]

        approve_data = test_client.post(
            f"/api/v1/prescriptions/{thread_id}/approve",
            json={
                "approved": False,
                "doctor_comments": "Necesita más estudios antes de medicar.",
            },
        ).json()

        assert approve_data["human_approved"] is False
        assert approve_data["is_paused_waiting_approval"] is False


# ── Allergy rejection path ────────────────────────────────────────────────────


class TestAllergyRejection:
    def test_safety_agent_flags_allergy(self, test_client_rejected):
        """When the safety LLM returns is_safe=False, safety_approved must be False."""
        pid = unique_pid()
        data = test_client_rejected.post(
            "/api/v1/prescriptions",
            json={
                "prescription_id": pid,
                "patient_symptoms": "Fiebre",
                "patient_allergies": ["Paracetamol"],
            },
        ).json()
        assert data["safety_approved"] is False
        assert data["is_paused_waiting_approval"] is True


# ── Error handling ────────────────────────────────────────────────────────────


class TestErrorHandling:
    def test_get_unknown_session_returns_404(self, test_client):
        resp = test_client.get("/api/v1/prescriptions/session-nonexistent-xyz")
        assert resp.status_code == 404

    def test_approve_unknown_session_returns_404(self, test_client):
        resp = test_client.post(
            "/api/v1/prescriptions/session-nonexistent-xyz/approve",
            json={"approved": True, "doctor_comments": "OK"},
        )
        assert resp.status_code == 404


# ── DB persistence ────────────────────────────────────────────────────────────


class TestDatabasePersistence:
    def test_prescription_record_created_in_db(self, test_client, db_session: Session):
        pid = unique_pid()
        test_client.post(
            "/api/v1/prescriptions",
            json={
                "prescription_id": pid,
                "patient_symptoms": "Náuseas",
                "patient_allergies": [],
            },
        )
        record = db_session.query(PrescriptionRecord).filter_by(prescription_id=pid).first()
        assert record is not None
        assert record.patient_symptoms == "Náuseas"
        assert record.proposed_medication == "Paracetamol"
        assert record.status == "PENDING_APPROVAL"

    def test_audit_logs_persisted_to_db(self, test_client, db_session: Session):
        pid = unique_pid()
        test_client.post(
            "/api/v1/prescriptions",
            json={
                "prescription_id": pid,
                "patient_symptoms": "Mareos",
                "patient_allergies": [],
            },
        )
        audit_records = db_session.query(AuditLogRecord).filter_by(prescription_id=pid).all()
        # Triage + Safety = 2 entries after initial flow
        assert len(audit_records) >= 2
        steps = [r.step for r in audit_records]
        assert "TRIAGE_AGENT" in steps
        assert "SAFETY_AGENT" in steps

    def test_prescription_status_updated_after_approval(self, test_client, db_session: Session):
        pid = unique_pid()
        create_data = test_client.post(
            "/api/v1/prescriptions",
            json={
                "prescription_id": pid,
                "patient_symptoms": "Fiebre 39°C",
                "patient_allergies": [],
            },
        ).json()
        thread_id = create_data["thread_id"]

        test_client.post(
            f"/api/v1/prescriptions/{thread_id}/approve",
            json={"approved": True, "doctor_comments": "Aprobado."},
        )

        record = db_session.query(PrescriptionRecord).filter_by(prescription_id=pid).first()
        assert record is not None
        assert record.status == "APPROVED"
        assert record.human_approved is True
