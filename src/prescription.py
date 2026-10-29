"""The prescription read model, derived only from a verified audit chain.

Every clinical fact the API reports comes from a signed entry, never from a
table column. If the chain fails verification or holds unexpected steps or
details, the read model holds no clinical facts at all, so no reader can act on
forged data.
"""

import json
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, StrictBool

from src.audit import AuditEntry, verify_chain
from src.clinical import SafetyCheck, TriageRecommendation


class Step(StrEnum):
    TRIAGE = "TRIAGE_AGENT"
    SAFETY = "SAFETY_AGENT"
    DOCTOR = "HUMAN_APPROVAL"


class Status(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    INVALID = "INVALID"


class SignedTriage(TriageRecommendation):
    patient_symptoms: str
    patient_allergies: list[str]
    fhir_patient: str | None = None
    fhir_allergy_ids: list[str] | None = None


class DoctorDecision(BaseModel):
    approved: StrictBool
    comments: str


PENDING_STEPS = [Step.TRIAGE, Step.SAFETY]
DECIDED_STEPS = [Step.TRIAGE, Step.SAFETY, Step.DOCTOR]


@dataclass(frozen=True)
class Prescription:
    audit_valid: bool
    audit_detail: str
    created_at: str | None = None
    triage: SignedTriage | None = None
    safety: SafetyCheck | None = None
    decision: DoctorDecision | None = None

    @property
    def status(self) -> Status:
        if not self.audit_valid:
            return Status.INVALID
        if self.decision is None:
            return Status.PENDING_APPROVAL
        return Status.APPROVED if self.decision.approved else Status.REJECTED


def read_prescription(
    chain: list[AuditEntry], *, key: bytes, prescription_id: str, head_hash: str, seal: str
) -> Prescription:
    check = verify_chain(
        chain, key=key, prescription_id=prescription_id, head_hash=head_hash, seal=seal
    )
    if not check.valid:
        return Prescription(False, check.detail)
    steps = [entry["step"] for entry in chain]
    if steps not in (PENDING_STEPS, DECIDED_STEPS):
        return Prescription(False, f"Unexpected audit steps: {steps}.")
    try:
        details = [json.loads(entry["details"]) for entry in chain]
        return Prescription(
            audit_valid=True,
            audit_detail=check.detail,
            created_at=chain[0]["timestamp"],
            triage=SignedTriage.model_validate(details[0]),
            safety=SafetyCheck.model_validate(details[1]),
            decision=DoctorDecision.model_validate(details[2]) if len(details) == 3 else None,
        )
    except ValueError as error:
        return Prescription(False, f"Signed details do not match the expected schema: {error}")
