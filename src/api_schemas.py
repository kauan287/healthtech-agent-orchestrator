from pydantic import BaseModel


class CreatePrescriptionRequest(BaseModel):
    prescription_id: str
    patient_symptoms: str
    patient_allergies: list[str]


class DoctorApprovalRequest(BaseModel):
    approved: bool
    doctor_comments: str


class PrescriptionResponse(BaseModel):
    thread_id: str
    prescription_id: str
    proposed_medication: str | None
    dosage: str | None
    safety_approved: bool
    risk_notes: str | None
    human_approved: bool | None
    doctor_comments: str | None
    is_paused_waiting_approval: bool
    audit_chain_valid: bool
