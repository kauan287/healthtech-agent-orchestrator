"""
Unit tests for Pydantic schemas (src/schemas.py and src/api_schemas.py).

Fast, dependency-free validation of model constraints.
"""
import pytest
from pydantic import ValidationError

from src.schemas import SafetyCheck, TriageRecommendation
from src.api_schemas import (
    CreatePrescriptionRequest,
    DoctorApprovalRequest,
    PrescriptionResponse,
)


class TestTriageRecommendation:
    def test_valid_schema_instantiates(self):
        obj = TriageRecommendation(
            proposed_medication="Paracetamol",
            dosage="500mg cada 8 horas",
            reasoning="Fiebre sin contraindicaciones.",
            urgency_level="BAJO",
        )
        assert obj.proposed_medication == "Paracetamol"
        assert obj.urgency_level == "BAJO"

    def test_missing_field_raises_validation_error(self):
        with pytest.raises(ValidationError):
            TriageRecommendation(
                proposed_medication="Paracetamol",
                # dosage, reasoning, urgency_level are missing
            )

    def test_all_fields_present_in_model_json(self):
        obj = TriageRecommendation(
            proposed_medication="Ibuprofeno",
            dosage="400mg",
            reasoning="Dolor leve.",
            urgency_level="MEDIO",
        )
        data = obj.model_dump()
        assert set(data.keys()) == {"proposed_medication", "dosage", "reasoning", "urgency_level"}


class TestSafetyCheck:
    def test_safe_true(self):
        obj = SafetyCheck(is_safe=True, risk_notes="Sin alergias.")
        assert obj.is_safe is True

    def test_safe_false(self):
        obj = SafetyCheck(is_safe=False, risk_notes="Alergia detectada.")
        assert obj.is_safe is False

    def test_missing_risk_notes_raises(self):
        with pytest.raises(ValidationError):
            SafetyCheck(is_safe=True)  # risk_notes required


class TestCreatePrescriptionRequest:
    def test_valid_request(self):
        req = CreatePrescriptionRequest(
            prescription_id="RX-001",
            patient_symptoms="Fiebre 38°C",
            patient_allergies=["Ibuprofeno"],
        )
        assert req.prescription_id == "RX-001"
        assert req.patient_allergies == ["Ibuprofeno"]

    def test_empty_allergies_list_is_valid(self):
        req = CreatePrescriptionRequest(
            prescription_id="RX-002",
            patient_symptoms="Dolor de cabeza",
            patient_allergies=[],
        )
        assert req.patient_allergies == []

    def test_missing_required_fields_raise(self):
        with pytest.raises(ValidationError):
            CreatePrescriptionRequest(prescription_id="RX-003")


class TestDoctorApprovalRequest:
    def test_approved_true(self):
        req = DoctorApprovalRequest(approved=True, doctor_comments="Todo correcto.")
        assert req.approved is True

    def test_approved_false(self):
        req = DoctorApprovalRequest(approved=False, doctor_comments="Contraindicado.")
        assert req.approved is False


class TestPrescriptionResponse:
    def test_response_serialisation(self):
        resp = PrescriptionResponse(
            thread_id="session-RX-001",
            prescription_id="RX-001",
            proposed_medication="Paracetamol",
            dosage="500mg",
            safety_approved=True,
            risk_notes=None,
            human_approved=None,
            doctor_comments=None,
            is_paused_waiting_approval=True,
            audit_chain_valid=True,
        )
        data = resp.model_dump()
        assert data["thread_id"] == "session-RX-001"
        assert data["is_paused_waiting_approval"] is True
        assert data["audit_chain_valid"] is True
