from fastapi import FastAPI, HTTPException
from dotenv import load_dotenv

from src.state import PrescriptionState
from src.graph import create_app
from src.audit import create_audit_entry, verify_audit_chain
from src.api_schemas import (
    CreatePrescriptionRequest, 
    DoctorApprovalRequest, 
    PrescriptionResponse
)

load_dotenv()

app = FastAPI(
    title="HealthTech Agent Orchestrator API",
    description="Backend de orquestación de agentes IA con soporte Human-in-the-Loop y Logs Inmutables.",
    version="1.0.0"
)

# Instanciamos el grafo
orchestrator_app = create_app(interrupt_after=["safety_agent"])


@app.post("/api/v1/prescriptions", response_model=PrescriptionResponse)
def start_prescription_flow(req: CreatePrescriptionRequest):
    """
    Inicia la evaluación de los agentes (Triaje + Seguridad) hasta la pausa médica.
    """
    thread_id = f"session-{req.prescription_id}"
    config = {"configurable": {"thread_id": thread_id}}

    initial_input: PrescriptionState = {
        "prescription_id": req.prescription_id,
        "patient_symptoms": req.patient_symptoms,
        "patient_allergies": req.patient_allergies,
        "proposed_medication": None,
        "dosage": None,
        "safety_approved": False,
        "risk_notes": None,
        "human_approved": None,
        "doctor_comments": None,
        "audit_logs": []
    }

    # Ejecutar agentes hasta la interrupción HITL
    for _ in orchestrator_app.stream(initial_input, config):
        pass

    state_values = orchestrator_app.get_state(config).values
    audit_valid, _ = verify_audit_chain(state_values.get("audit_logs", []))

    return PrescriptionResponse(
        thread_id=thread_id,
        prescription_id=state_values["prescription_id"],
        proposed_medication=state_values.get("proposed_medication"),
        dosage=state_values.get("dosage"),
        safety_approved=state_values.get("safety_approved", False),
        risk_notes=state_values.get("risk_notes"),
        human_approved=state_values.get("human_approved"),
        doctor_comments=state_values.get("doctor_comments"),
        is_paused_waiting_approval=True,
        audit_chain_valid=audit_valid
    )


@app.get("/api/v1/prescriptions/{thread_id}", response_model=PrescriptionResponse)
def get_prescription_status(thread_id: str):
    """
    Obtiene el estado actual de la sesión y verifica la cadena de auditoría.
    """
    config = {"configurable": {"thread_id": thread_id}}
    state = orchestrator_app.get_state(config)

    if not state.values:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    state_values = state.values
    audit_valid, _ = verify_audit_chain(state_values.get("audit_logs", []))
    
    # Comprobar si el grafo sigue pausado esperando aprobación humana
    is_paused = len(state.next) > 0

    return PrescriptionResponse(
        thread_id=thread_id,
        prescription_id=state_values["prescription_id"],
        proposed_medication=state_values.get("proposed_medication"),
        dosage=state_values.get("dosage"),
        safety_approved=state_values.get("safety_approved", False),
        risk_notes=state_values.get("risk_notes"),
        human_approved=state_values.get("human_approved"),
        doctor_comments=state_values.get("doctor_comments"),
        is_paused_waiting_approval=is_paused,
        audit_chain_valid=audit_valid
    )


@app.post("/api/v1/prescriptions/{thread_id}/approve", response_model=PrescriptionResponse)
def approve_prescription(thread_id: str, req: DoctorApprovalRequest):
    """
    Registra la decisión del médico, actualiza el hash encadenado y finaliza el flujo.
    """
    config = {"configurable": {"thread_id": thread_id}}
    current_state = orchestrator_app.get_state(config)

    if not current_state.values:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    # Obtener el último log para encadenar el hash del humano
    existing_logs = current_state.values.get("audit_logs", [])
    last_log = existing_logs[-1] if existing_logs else None

    human_log = create_audit_entry(
        previous_log=last_log,
        step="HUMAN_APPROVAL",
        action="Doctor Decision Recorded via API",
        details={
            "approved": req.approved,
            "comments": req.doctor_comments
        }
    )

    # Reanudar el estado
    orchestrator_app.update_state(
        config,
        {
            "human_approved": req.approved,
            "doctor_comments": req.doctor_comments,
            "audit_logs": [human_log]
        },
        as_node="safety_agent"
    )

    # Correr hasta el final
    for _ in orchestrator_app.stream(None, config):
        pass

    final_values = orchestrator_app.get_state(config).values
    audit_valid, _ = verify_audit_chain(final_values.get("audit_logs", []))

    return PrescriptionResponse(
        thread_id=thread_id,
        prescription_id=final_values["prescription_id"],
        proposed_medication=final_values.get("proposed_medication"),
        dosage=final_values.get("dosage"),
        safety_approved=final_values.get("safety_approved", False),
        risk_notes=final_values.get("risk_notes"),
        human_approved=final_values.get("human_approved"),
        doctor_comments=final_values.get("doctor_comments"),
        is_paused_waiting_approval=False,
        audit_chain_valid=audit_valid
    )