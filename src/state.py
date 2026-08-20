import operator
from typing import Annotated, TypedDict


class PrescriptionState(TypedDict):
    # Identificador único de la petición
    prescription_id: str

    # Entrada inicial del paciente/médico
    patient_symptoms: str
    patient_allergies: list[str]

    # Propuesta generada por el Agente 1 (Triaje)
    proposed_medication: str | None
    dosage: str | None

    # Análisis del Agente 2 (Seguridad)
    safety_approved: bool
    risk_notes: str | None

    # Control Humano (HITL)
    human_approved: bool | None
    doctor_comments: str | None

    # Historial / Logs inmutables acumulativos
    audit_logs: Annotated[list[dict], operator.add]
