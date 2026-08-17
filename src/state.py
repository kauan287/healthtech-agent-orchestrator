from typing import TypedDict, Annotated, List, Optional
import operator

class PrescriptionState(TypedDict):
    # Identificador único de la petición
    prescription_id: str
    
    # Entrada inicial del paciente/médico
    patient_symptoms: str
    patient_allergies: List[str]
    
    # Propuesta generada por el Agente 1 (Triaje)
    proposed_medication: Optional[str]
    dosage: Optional[str]
    
    # Análisis del Agente 2 (Seguridad)
    safety_approved: bool
    risk_notes: Optional[str]
    
    # Control Humano (HITL)
    human_approved: Optional[bool]
    doctor_comments: Optional[str]
    
    # Historial / Logs inmutables acumulativos
    audit_logs: Annotated[List[dict], operator.add]
    