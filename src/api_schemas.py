from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

from src.prescription import Status


def _storable(value: str) -> str:
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("must be valid Unicode text") from None
    return value


Text = Annotated[str, AfterValidator(_storable)]


class CreatePrescriptionRequest(BaseModel):
    prescription_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    patient_symptoms: Text = Field(min_length=1, max_length=5000)
    patient_allergies: list[Annotated[Text, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=50
    )


class DoctorApprovalRequest(BaseModel):
    approved: bool
    doctor_comments: Text = Field(max_length=5000)


class PrescriptionResponse(BaseModel):
    """Read by SmartDispenser Core. Add fields freely, never rename or remove one."""

    thread_id: str
    prescription_id: str
    status: Status
    proposed_medication: str | None
    dosage: str | None
    safety_approved: bool
    risk_notes: str | None
    human_approved: bool | None
    doctor_comments: str | None
    is_paused_waiting_approval: bool
    audit_chain_valid: bool


class AuditEntryResponse(BaseModel):
    step: str
    action: str
    timestamp: str
    details: str | None = Field(
        description="The exact JSON text that the entry's MAC signs. Null when the chain is invalid."
    )
    previous_hash: str
    hash: str


class AuditTrailResponse(BaseModel):
    prescription_id: str
    valid: bool
    detail: str
    entries: list[AuditEntryResponse]
