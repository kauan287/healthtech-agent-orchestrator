from pydantic import BaseModel
from typing import List, Optional

class CreatePrescriptionRequest(BaseModel):
    prescription_id: str
    patient_symptoms: str
    patient_allergies: List[str]

class DoctorApprovalRequest(BaseModel):
    approved: bool
    doctor_comments: str

class PrescriptionResponse(BaseModel):
    thread_id: str
    prescription_id: str
    proposed_medication: Optional[str]
    dosage: Optional[str]
    safety_approved: bool
    risk_notes: Optional[str]
    human_approved: Optional[bool]
    doctor_comments: Optional[str]
    is_paused_waiting_approval: bool
    audit_chain_valid: bool