import base64
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, tzinfo
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from fhir.resources.R4B.bundle import Bundle
from sqlalchemy import Engine, create_engine, text

from src.config import Settings
from src.fhir import FhirAllergy, FhirClient, FhirPatientNotFound, FhirUnavailable
from src.server import create_api

BASE = "http://fhir.test/baseR4"


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> "FrozenDatetime":
        return cls(2026, 3, 14, 9, 30, tzinfo=UTC)


P1: dict[str, Any] = {
    "resourceType": "Patient",
    "id": "p1",
    "name": [{"family": "Test", "given": ["Synthetic"]}],
}


def allergy(resource_id: str, code: dict[str, Any], **statuses: str) -> dict[str, Any]:
    resource: dict[str, Any] = {
        "resourceType": "AllergyIntolerance",
        "id": resource_id,
        "patient": {"reference": "Patient/p1"},
        "code": code,
    }
    for field, status in statuses.items():
        resource[field] = {"coding": [{"code": status}]}
    return resource


PARACETAMOL = allergy("a1", {"text": "Paracetamol"}, clinicalStatus="active")
OLD_IBUPROFENO = allergy("a2", {"text": "Ibuprofeno"}, clinicalStatus="inactive")
REFUTED_PENICILINA = allergy("a3", {"text": "Penicilina"}, verificationStatus="refuted")


class FakeFhirServer:
    """Serves Patient reads and paged AllergyIntolerance searches like a FHIR R4 server."""

    def __init__(self, patients: dict[str, list[list[dict[str, Any]]]]) -> None:
        self.patients = patients
        self.status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.status != 200:
            return httpx.Response(self.status, text="Internal error")
        path = request.url.path.removeprefix("/baseR4")
        if path.startswith("/Patient/"):
            patient_id = path.removeprefix("/Patient/")
            if patient_id not in self.patients:
                return httpx.Response(404, json={"resourceType": "OperationOutcome", "issue": []})
            return httpx.Response(200, json={**P1, "id": patient_id})
        params = request.url.params
        if path == "/AllergyIntolerance" and params.get("_count") == "100":
            return self._page(params["patient"].removeprefix("Patient/"), 0)
        if path == "" and "_page" in params:
            return self._page(params["_getpages"], int(params["_page"]))
        return httpx.Response(400, json={"resourceType": "OperationOutcome", "issue": []})

    def _page(self, patient_id: str, page: int) -> httpx.Response:
        pages = self.patients[patient_id]
        bundle: dict[str, Any] = {
            "resourceType": "Bundle",
            "type": "searchset",
            "entry": [{"resource": resource} for resource in pages[page]],
            "link": [{"relation": "self", "url": f"{BASE}/AllergyIntolerance"}],
        }
        if page + 1 < len(pages):
            bundle["link"].append(
                {"relation": "next", "url": f"{BASE}?_getpages={patient_id}&_page={page + 1}"}
            )
        return httpx.Response(200, json=bundle)


@pytest.fixture
def fhir_server() -> FakeFhirServer:
    return FakeFhirServer({"p1": [[PARACETAMOL, OLD_IBUPROFENO, REFUTED_PENICILINA]]})


@pytest.fixture
def fhir_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"FHIR_BASE_URL": BASE})


@pytest.fixture
def fhir_client(fhir_settings: Settings, fhir_server: FakeFhirServer) -> Iterator[TestClient]:
    with TestClient(create_api(fhir_settings, httpx.MockTransport(fhir_server))) as client:
        yield client


@pytest.fixture
def fhir_db(fhir_settings: Settings, fhir_client: TestClient) -> Engine:
    return create_engine(fhir_settings.DATABASE_URL)


def from_fhir(client: TestClient, patient_id: str = "p1") -> Any:
    return client.post(
        "/api/v1/prescriptions/from-fhir",
        json={
            "prescription_id": "RX-FHIR-1",
            "fhir_patient_id": patient_id,
            "patient_symptoms": "Fiebre de 39 °C y dolor articular",
        },
    )


def context_of(server: FakeFhirServer, patient_id: str = "p1") -> list[FhirAllergy]:
    client = FhirClient(BASE, 5, httpx.MockTransport(server))
    return client.patient_context(patient_id).allergies


def test_current_allergies_drive_the_safety_check(fhir_client: TestClient) -> None:
    response = from_fhir(fhir_client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["patient_allergies"], body["safety_approved"], body["fhir_patient"]) == (
        ["Paracetamol"],
        False,
        "Patient/p1",
    )
    assert body["risk_notes"] == "Contraindicado: alergia a Paracetamol."
    trail = fhir_client.get("/api/v1/prescriptions/session-RX-FHIR-1/audit").json()
    assert '"fhir_allergy_ids":["a1"],"fhir_patient":"Patient/p1"' in trail["entries"][0]["details"]


def test_allergies_on_later_pages_are_included(fhir_server: FakeFhirServer) -> None:
    fhir_server.patients["p1"] = [[OLD_IBUPROFENO], [], [PARACETAMOL]]
    assert context_of(fhir_server) == [FhirAllergy("a1", "Paracetamol")]


def test_more_than_five_pages_is_refused_rather_than_truncated(
    fhir_server: FakeFhirServer,
) -> None:
    fhir_server.patients["p1"] = [[]] * 5 + [[PARACETAMOL]]
    with pytest.raises(FhirUnavailable, match="more than 5 pages"):
        context_of(fhir_server)


def test_text_falls_back_to_the_first_coding(
    fhir_server: FakeFhirServer, caplog: pytest.LogCaptureFixture
) -> None:
    fhir_server.patients["p1"] = [
        [
            allergy("t", {"text": " Amoxicilina ", "coding": [{"display": "Amoxicillin"}]}),
            allergy("d", {"coding": [{"code": "387517004", "display": "Paracetamol"}]}),
            allergy("c", {"coding": [{"system": "http://snomed.info/sct", "code": "372687004"}]}),
            allergy("none", {"coding": [{"system": "http://snomed.info/sct"}]}),
            allergy("unconfirmed", {"text": "Látex"}, verificationStatus="unconfirmed"),
            allergy("resolved", {"text": "Aspirina"}, clinicalStatus="resolved"),
            allergy("error", {"text": "Codeína"}, verificationStatus="entered-in-error"),
        ]
    ]
    with caplog.at_level(logging.WARNING, logger="src.fhir"):
        allergies = context_of(fhir_server)
    assert allergies == [
        FhirAllergy("t", "Amoxicilina"),
        FhirAllergy("d", "Paracetamol"),
        FhirAllergy("c", "372687004"),
        FhirAllergy("unconfirmed", "Látex"),
    ]
    assert caplog.messages == ["Skipped 1 current allergies of Patient/p1 with no usable text."]


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(200, text="<html>maintenance</html>"),
        httpx.Response(200, json={"resourceType": "Observation", "status": "final"}),
        httpx.Response(401, json={"resourceType": "OperationOutcome", "issue": []}),
    ],
)
def test_a_reply_that_is_not_the_resource_is_unavailable(reply: httpx.Response) -> None:
    client = FhirClient(BASE, 5, httpx.MockTransport(lambda request: reply))
    with pytest.raises(FhirUnavailable):
        client.patient_context("p1")


def test_network_errors_are_unavailable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(FhirUnavailable, match="connection refused"):
        FhirClient(BASE, 5, httpx.MockTransport(refuse)).patient_context("p1")


def test_a_next_link_to_another_host_is_not_followed(fhir_server: FakeFhirServer) -> None:
    def redirect_off_server(request: httpx.Request) -> httpx.Response:
        response = fhir_server(request)
        if request.url.path == "/baseR4/AllergyIntolerance":
            return httpx.Response(
                200,
                json={
                    "resourceType": "Bundle",
                    "type": "searchset",
                    "link": [{"relation": "next", "url": "http://evil.test/steal"}],
                },
            )
        return response

    client = FhirClient(BASE, 5, httpx.MockTransport(redirect_off_server))
    with pytest.raises(FhirUnavailable, match="evil.test"):
        client.patient_context("p1")


def test_an_invalid_id_never_reaches_the_server() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request {request.url}")

    with pytest.raises(FhirPatientNotFound):
        FhirClient(BASE, 5, httpx.MockTransport(fail)).patient_context("../Patient/p1")


def test_unknown_patient_is_unprocessable(fhir_client: TestClient) -> None:
    response = from_fhir(fhir_client, "nobody")
    assert (response.status_code, response.json()) == (
        422,
        {"detail": "FHIR patient nobody not found."},
    )


def test_invalid_patient_id_is_unprocessable(fhir_client: TestClient) -> None:
    response = from_fhir(fhir_client, "p1/_history")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "fhir_patient_id"]


def test_fhir_server_error_is_a_bad_gateway(
    fhir_client: TestClient, fhir_server: FakeFhirServer
) -> None:
    fhir_server.status = 500
    response = from_fhir(fhir_client)
    assert (response.status_code, response.json()) == (502, {"detail": "FHIR server unavailable."})
    assert fhir_client.get("/api/v1/prescriptions/session-RX-FHIR-1").status_code == 404


def test_without_a_fhir_server_the_endpoint_is_unavailable(client: TestClient) -> None:
    response = from_fhir(client)
    assert (response.status_code, response.json()) == (
        503,
        {"detail": "FHIR server not configured."},
    )


def test_more_allergies_than_a_prescription_holds_is_unprocessable(
    fhir_client: TestClient, fhir_server: FakeFhirServer
) -> None:
    fhir_server.patients["p1"] = [[allergy(f"x{n}", {"text": f"Alérgeno {n}"}) for n in range(51)]]
    response = from_fhir(fhir_client)
    assert (response.status_code, response.json()) == (
        422,
        {"detail": "The allergies of Patient/p1 exceed the prescription limits."},
    )


def test_duplicate_prescription_is_a_conflict(fhir_client: TestClient) -> None:
    assert from_fhir(fhir_client).status_code == 201
    response = from_fhir(fhir_client)
    assert (response.status_code, response.json()) == (
        409,
        {"detail": "Prescription RX-FHIR-1 already exists."},
    )


def test_tampered_fhir_patient_reads_as_invalid(fhir_client: TestClient, fhir_db: Engine) -> None:
    from_fhir(fhir_client)
    with fhir_db.begin() as conn:
        conn.execute(
            text("UPDATE audit_logs SET details = replace(details, 'Patient/p1', 'Patient/p2')")
        )
    body = fhir_client.get("/api/v1/prescriptions/session-RX-FHIR-1").json()
    assert (body["status"], body["fhir_patient"], body["patient_allergies"]) == (
        "INVALID",
        None,
        None,
    )


EXPORTED_APPROVED: dict[str, Any] = {
    "resourceType": "Bundle",
    "type": "collection",
    "entry": [
        {
            "fullUrl": "urn:uuid:10208174-51f4-5790-b132-1568ba7d52f1",
            "resource": {
                "resourceType": "MedicationRequest",
                "identifier": [{"system": "urn:healthtech:prescription", "value": "RX-FHIR-1"}],
                "status": "active",
                "intent": "order",
                "medicationCodeableConcept": {"text": "Paracetamol"},
                "subject": {"reference": "Patient/p1"},
                "authoredOn": "2026-03-14T09:30:00Z",
                "note": [
                    {
                        "authorString": "Safety agent",
                        "text": "Contraindicado: alergia a Paracetamol.",
                    },
                    {"authorString": "Doctor", "text": "Cambiar a metamizol."},
                ],
                "dosageInstruction": [{"text": "500 mg cada 8 horas"}],
            },
        },
        {
            "fullUrl": "urn:uuid:7f1fdc79-34fe-58f7-9ccb-dc949608fed4",
            "resource": {
                "resourceType": "Provenance",
                "target": [{"reference": "urn:uuid:10208174-51f4-5790-b132-1568ba7d52f1"}],
                "recorded": "2026-03-14T09:30:00Z",
                "agent": [{"who": {"display": "HealthTech Agent Orchestrator"}}],
                "signature": [
                    {
                        "type": [
                            {
                                "system": "urn:iso-astm:E1762-95:2013",
                                "code": "1.2.840.10065.1.12.1.5",
                                "display": "Verification Signature",
                            }
                        ],
                        "when": "2026-03-14T09:30:00Z",
                        "who": {"display": "HealthTech Agent Orchestrator"},
                        "sigFormat": "application/hmac-sha256",
                    }
                ],
            },
        },
    ],
}


def export(client: TestClient) -> Any:
    response = client.get("/api/v1/prescriptions/session-RX-FHIR-1/fhir")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/fhir+json"
    Bundle.model_validate_json(response.content)
    return response.json()


def test_export_maps_the_review_to_a_signed_medication_request(
    fhir_client: TestClient, fhir_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.audit.datetime", FrozenDatetime)
    monkeypatch.setattr("src.server.datetime", FrozenDatetime)
    from_fhir(fhir_client)
    pending = export(fhir_client)["entry"][0]["resource"]
    assert (pending["status"], pending["intent"]) == ("draft", "proposal")

    fhir_client.post(
        "/api/v1/prescriptions/session-RX-FHIR-1/approve",
        json={"approved": True, "doctor_comments": "Cambiar a metamizol."},
    )
    bundle = export(fhir_client)
    with fhir_db.connect() as conn:
        head = conn.execute(text("SELECT head_hash FROM prescriptions")).scalar_one()
    signature = bundle["entry"][1]["resource"]["signature"][0]
    assert base64.b64decode(signature.pop("data"), validate=True).hex() == head
    assert bundle == EXPORTED_APPROVED


def test_export_without_fhir_names_the_patient_by_prescription(client: TestClient) -> None:
    client.post(
        "/api/v1/prescriptions",
        json={
            "prescription_id": "RX-FHIR-1",
            "patient_symptoms": "Fiebre",
            "patient_allergies": [],
        },
    )
    client.post(
        "/api/v1/prescriptions/session-RX-FHIR-1/approve",
        json={"approved": False, "doctor_comments": ""},
    )
    request = export(client)["entry"][0]["resource"]
    assert (request["status"], request["intent"], request["subject"]) == (
        "cancelled",
        "proposal",
        {"display": "Patient of RX-FHIR-1"},
    )
    assert request["note"] == [
        {"authorString": "Safety agent", "text": "Sin conflictos con las alergias conocidas."}
    ]


def test_export_refuses_a_tampered_chain(fhir_client: TestClient, fhir_db: Engine) -> None:
    from_fhir(fhir_client)
    with fhir_db.begin() as conn:
        conn.execute(text("UPDATE audit_logs SET details = replace(details, 'a1', 'a9')"))
    response = fhir_client.get("/api/v1/prescriptions/session-RX-FHIR-1/fhir")
    assert (response.status_code, response.json()) == (
        409,
        {"detail": "Audit chain is invalid; the prescription is not exported."},
    )


def test_export_of_an_unknown_prescription_is_not_found(fhir_client: TestClient) -> None:
    assert fhir_client.get("/api/v1/prescriptions/session-nope/fhir").status_code == 404
