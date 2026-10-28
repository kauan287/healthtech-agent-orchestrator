import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime, tzinfo
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from src.clinical import DemoClinicalModel, SafetyCheck, TriageRecommendation
from src.db import PrescriptionRepository

FEVER = {
    "prescription_id": "RX-001",
    "patient_symptoms": "Fiebre de 39 °C y dolor articular",
    "patient_allergies": ["Ibuprofeno"],
}
THREAD = "session-RX-001"
NO_FACTS = {
    "status": "INVALID",
    "proposed_medication": None,
    "dosage": None,
    "safety_approved": False,
    "risk_notes": None,
    "human_approved": None,
    "doctor_comments": None,
    "is_paused_waiting_approval": False,
    "audit_chain_valid": False,
    "patient_symptoms": None,
    "patient_allergies": None,
    "urgency_level": None,
    "triage_reasoning": None,
    "created_at": None,
}


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> "FrozenDatetime":
        return cls(2026, 3, 14, 9, 30, tzinfo=UTC)


def create(client: TestClient, **overrides: Any) -> Any:
    response = client.post("/api/v1/prescriptions", json={**FEVER, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def decide(client: TestClient, approved: bool, thread: str = THREAD) -> Any:
    return client.post(
        f"/api/v1/prescriptions/{thread}/approve",
        json={"approved": approved, "doctor_comments": "Revisado."},
    )


def read(client: TestClient) -> Any:
    return client.get(f"/api/v1/prescriptions/{THREAD}").json()


def sql(raw_db: Engine, statement: str) -> None:
    with raw_db.begin() as conn:
        conn.execute(text(statement))


def test_agents_propose_and_wait_for_the_doctor(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.audit.datetime", FrozenDatetime)
    expected = {
        "thread_id": THREAD,
        "prescription_id": "RX-001",
        "status": "PENDING_APPROVAL",
        "proposed_medication": "Paracetamol",
        "dosage": "500 mg cada 8 horas",
        "safety_approved": True,
        "risk_notes": "Sin conflictos con las alergias conocidas.",
        "human_approved": None,
        "doctor_comments": None,
        "is_paused_waiting_approval": True,
        "audit_chain_valid": True,
        "patient_symptoms": "Fiebre de 39 °C y dolor articular",
        "patient_allergies": ["Ibuprofeno"],
        "urgency_level": "MEDIO",
        "triage_reasoning": "Analgésico y antipirético de primera línea.",
        "created_at": "2026-03-14T09:30:00+00:00",
    }
    assert create(client) == expected
    assert read(client) == expected


def test_safety_agent_flags_an_allergy_conflict(client: TestClient) -> None:
    rx = create(client, patient_allergies=["Paracetamol"])
    assert (rx["safety_approved"], rx["risk_notes"]) == (
        False,
        "Contraindicado: alergia a Paracetamol.",
    )


def test_doctor_approval_completes_the_review(client: TestClient) -> None:
    create(client)
    response = decide(client, approved=True)
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["human_approved"], body["doctor_comments"]) == (
        "APPROVED",
        True,
        "Revisado.",
    )
    assert (body["is_paused_waiting_approval"], body["audit_chain_valid"]) == (False, True)
    assert read(client) == body


def test_audit_trail_lists_every_step_and_verifies(client: TestClient) -> None:
    create(client)
    decide(client, approved=False)
    trail = client.get(f"/api/v1/prescriptions/{THREAD}/audit").json()
    assert (trail["valid"], trail["detail"]) == (True, "3 entries verified.")
    assert [e["step"] for e in trail["entries"]] == [
        "TRIAGE_AGENT",
        "SAFETY_AGENT",
        "HUMAN_APPROVAL",
    ]
    assert json.loads(trail["entries"][0]["details"])["patient_allergies"] == ["Ibuprofeno"]
    assert trail["entries"][2]["details"] == '{"approved":false,"comments":"Revisado."}'


def test_duplicate_prescription_id_is_a_conflict(client: TestClient) -> None:
    create(client)
    response = client.post("/api/v1/prescriptions", json=FEVER)
    assert (response.status_code, response.json()["detail"]) == (
        409,
        "Prescription RX-001 already exists.",
    )


def test_a_decision_cannot_be_changed(client: TestClient) -> None:
    create(client)
    decide(client, approved=False)
    response = decide(client, approved=True)
    assert (response.status_code, response.json()["detail"]) == (
        409,
        "Prescription is already REJECTED.",
    )
    assert read(client)["human_approved"] is False


def test_concurrent_decisions_record_exactly_one(client: TestClient) -> None:
    create(client)
    barrier = threading.Barrier(2)
    codes: dict[bool, int] = {}

    def submit(approved: bool) -> None:
        barrier.wait()
        codes[approved] = decide(client, approved).status_code

    threads = [threading.Thread(target=submit, args=(flag,)) for flag in (True, False)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(codes.values()) == [200, 409]
    winner = next(flag for flag, code in codes.items() if code == 200)
    assert (read(client)["human_approved"], read(client)["audit_chain_valid"]) == (winner, True)


def test_concurrent_creates_with_one_id_store_exactly_one(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    barrier = threading.Barrier(2)
    original = DemoClinicalModel.triage

    def both_past_the_duplicate_check(self: DemoClinicalModel, *args: Any) -> TriageRecommendation:
        barrier.wait()
        return original(self, *args)

    monkeypatch.setattr(DemoClinicalModel, "triage", both_past_the_duplicate_check)
    codes: list[int] = []

    def submit() -> None:
        codes.append(client.post("/api/v1/prescriptions", json=FEVER).status_code)

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(codes) == [201, 409]
    assert (
        client.get(f"/api/v1/prescriptions/{THREAD}/audit").json()["detail"]
        == "2 entries verified."
    )


def test_a_decision_committed_mid_read_is_not_reported_as_tampering(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    create(client)
    original = PrescriptionRepository.chain
    pending = [True]

    def decide_between_head_and_chain(self: PrescriptionRepository, prescription_id: str) -> Any:
        if pending.pop() if pending else False:
            assert decide(client, approved=True).status_code == 200
        return original(self, prescription_id)

    monkeypatch.setattr(PrescriptionRepository, "chain", decide_between_head_and_chain)
    body = read(client)
    assert (body["status"], body["audit_chain_valid"]) == ("APPROVED", True)


def test_pending_review_survives_a_restart(restartable_client: Callable[[], TestClient]) -> None:
    create(restartable_client())
    response = decide(restartable_client(), approved=True)
    assert (response.status_code, response.json()["status"]) == (200, "APPROVED")


def test_failed_agent_run_leaves_nothing_behind(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    original = DemoClinicalModel.check_safety

    def flaky(self: DemoClinicalModel, *args: Any) -> SafetyCheck:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("LLM timed out")
        return original(self, *args)

    monkeypatch.setattr(DemoClinicalModel, "check_safety", flaky)
    with pytest.raises(TimeoutError):
        client.post("/api/v1/prescriptions", json=FEVER)
    assert client.get(f"/api/v1/prescriptions/{THREAD}").status_code == 404

    create(client)
    trail = client.get(f"/api/v1/prescriptions/{THREAD}/audit").json()
    assert [e["step"] for e in trail["entries"]] == ["TRIAGE_AGENT", "SAFETY_AGENT"]


def test_edited_audit_entry_withholds_every_clinical_fact(
    client: TestClient, raw_db: Engine
) -> None:
    create(client)
    decide(client, approved=True)
    sql(
        raw_db,
        "UPDATE audit_logs SET details = replace(details, 'Paracetamol', 'Morfina')",
    )
    assert read(client) == {"thread_id": THREAD, "prescription_id": "RX-001", **NO_FACTS}
    trail = client.get(f"/api/v1/prescriptions/{THREAD}/audit").json()
    assert (trail["valid"], trail["detail"]) == (
        False,
        "Entry 0 (TRIAGE_AGENT) was modified after it was signed.",
    )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_logs SET details = '{}'",
        "UPDATE audit_logs SET details = '[]'",
        "UPDATE audit_logs SET details = 'not json'",
        "UPDATE audit_logs SET hash = 'é' WHERE step = 'SAFETY_AGENT'",
        "DELETE FROM audit_logs",
        "UPDATE prescriptions SET seal = 'x'",
    ],
)
def test_malformed_audit_rows_read_as_invalid_not_as_errors(
    client: TestClient, raw_db: Engine, statement: str
) -> None:
    create(client)
    sql(raw_db, statement)
    assert read(client) == {"thread_id": THREAD, "prescription_id": "RX-001", **NO_FACTS}
    trail = client.get(f"/api/v1/prescriptions/{THREAD}/audit")
    assert (trail.status_code, trail.json()["valid"]) == (200, False)
    assert [e["details"] for e in trail.json()["entries"]] == [None] * len(trail.json()["entries"])


def test_no_decision_on_a_tampered_chain(client: TestClient, raw_db: Engine) -> None:
    create(client)
    sql(
        raw_db,
        "UPDATE audit_logs SET details = replace(details, 'Paracetamol', 'Morfina')",
    )
    response = decide(client, approved=True)
    assert (response.status_code, response.json()["detail"]) == (
        409,
        "Audit chain is invalid: Entry 0 (TRIAGE_AGENT) was modified after it was signed.",
    )
    assert read(client)["status"] == "INVALID"


def test_deleting_the_decision_and_rewinding_the_head_reads_as_invalid(
    client: TestClient, raw_db: Engine
) -> None:
    create(client)
    decide(client, approved=False)
    safety_hash = client.get(f"/api/v1/prescriptions/{THREAD}/audit").json()["entries"][1]["hash"]
    sql(raw_db, "DELETE FROM audit_logs WHERE step = 'HUMAN_APPROVAL'")
    assert read(client)["status"] == "INVALID"

    sql(raw_db, f"UPDATE prescriptions SET head_hash = '{safety_hash}'")
    assert read(client) == {"thread_id": THREAD, "prescription_id": "RX-001", **NO_FACTS}
    response = decide(client, approved=True)
    assert (response.status_code, response.json()["detail"]) == (
        409,
        "Audit chain is invalid: The sealed chain head was modified.",
    )


def test_unknown_prescription_is_not_found(client: TestClient) -> None:
    assert client.get("/api/v1/prescriptions/session-missing").status_code == 404
    assert client.get("/api/v1/prescriptions/RX-001").status_code == 404
    assert decide(client, approved=True, thread="session-missing").status_code == 404
    assert client.get("/api/v1/prescriptions/session-missing/audit").status_code == 404


def test_prescription_id_must_be_url_safe(client: TestClient) -> None:
    response = client.post("/api/v1/prescriptions", json={**FEVER, "prescription_id": "RX 1/../x"})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "field", [{"patient_symptoms": "fiebre \ud800"}, {"patient_allergies": ["a\u0000b"]}]
)
def test_text_that_cannot_be_signed_or_stored_is_rejected(
    client: TestClient, field: dict[str, Any]
) -> None:
    body = json.dumps({**FEVER, **field}, ensure_ascii=True)
    response = client.post(
        "/api/v1/prescriptions", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422


def test_allergy_entries_are_bounded(client: TestClient) -> None:
    response = client.post(
        "/api/v1/prescriptions", json={**FEVER, "patient_allergies": ["x" * 201]}
    )
    assert response.status_code == 422


def test_readiness_checks_the_database(client: TestClient) -> None:
    assert client.get("/health/ready").json() == {"status": "ready"}


def test_response_keeps_the_fields_smartdispenser_reads(client: TestClient) -> None:
    """Mirror of OrchestratorSession in smartdispenser-core's HealthTechApiClient.cs."""
    body = client.get(f"/api/v1/prescriptions/{create(client)['thread_id']}").json()
    contract: dict[str, type | tuple[type, ...]] = {
        "thread_id": str,
        "prescription_id": str,
        "proposed_medication": (str, type(None)),
        "dosage": (str, type(None)),
        "safety_approved": bool,
        "risk_notes": (str, type(None)),
        "human_approved": (bool, type(None)),
        "doctor_comments": (str, type(None)),
        "is_paused_waiting_approval": bool,
        "audit_chain_valid": bool,
    }
    for field, kind in contract.items():
        assert isinstance(body[field], kind), field
