"""Export a verified prescription as an HL7 FHIR R4 Bundle.

The Bundle holds a MedicationRequest and a Provenance whose signature carries
the audit chain head, so a FHIR consumer can ask this service to verify it.
"""

import base64
import uuid
from datetime import datetime

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.provenance import Provenance

from src.audit import AuditEntry
from src.prescription import Prescription, Status

PRESCRIPTION_SYSTEM = "urn:healthtech:prescription"
ORCHESTRATOR = {"display": "HealthTech Agent Orchestrator"}
VERIFICATION_SIGNATURE = {
    "system": "urn:iso-astm:E1762-95:2013",
    "code": "1.2.840.10065.1.12.1.5",
    "display": "Verification Signature",
}

REQUEST_STATUS_AND_INTENT: dict[Status, tuple[str, str]] = {
    Status.PENDING_APPROVAL: ("draft", "proposal"),
    Status.APPROVED: ("active", "order"),
    Status.REJECTED: ("cancelled", "proposal"),
}


def _full_url(prescription_id: str, kind: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{PRESCRIPTION_SYSTEM}:{prescription_id}:{kind}").urn


def prescription_bundle(
    prescription_id: str, rx: Prescription, head: AuditEntry, recorded: datetime
) -> Bundle:
    if rx.triage is None or rx.safety is None or rx.created_at is None:
        raise ValueError("Only a verified prescription can be exported.")
    status, intent = REQUEST_STATUS_AND_INTENT[rx.status]
    notes = [{"authorString": "Safety agent", "text": rx.safety.risk_notes}]
    if rx.decision is not None and rx.decision.comments:
        notes.append({"authorString": "Doctor", "text": rx.decision.comments})
    request_url = _full_url(prescription_id, "MedicationRequest")
    request = MedicationRequest.model_validate(
        {
            "identifier": [{"system": PRESCRIPTION_SYSTEM, "value": prescription_id}],
            "status": status,
            "intent": intent,
            "medicationCodeableConcept": {"text": rx.triage.proposed_medication},
            "dosageInstruction": [{"text": rx.triage.dosage}],
            "subject": (
                {"reference": rx.triage.fhir_patient}
                if rx.triage.fhir_patient
                else {"display": f"Patient of {prescription_id}"}
            ),
            "authoredOn": rx.created_at,
            "note": notes,
        }
    )
    provenance = Provenance.model_validate(
        {
            "target": [{"reference": request_url}],
            "recorded": recorded,
            "agent": [{"who": ORCHESTRATOR}],
            "signature": [
                {
                    "type": [VERIFICATION_SIGNATURE],
                    "when": head["timestamp"],
                    "who": ORCHESTRATOR,
                    "sigFormat": "application/hmac-sha256",
                    "data": base64.b64encode(bytes.fromhex(head["hash"])).decode(),
                }
            ],
        }
    )
    return Bundle.model_validate(
        {
            "type": "collection",
            "entry": [
                {"fullUrl": request_url, "resource": request},
                {"fullUrl": _full_url(prescription_id, "Provenance"), "resource": provenance},
            ],
        }
    )
