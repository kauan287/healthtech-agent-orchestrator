from datetime import datetime, timezone
from dotenv import load_dotenv
from langchain_groq import ChatGroq

from src.state import PrescriptionState
from src.schemas import TriageRecommendation, SafetyCheck
from src.audit import create_audit_entry  # Importamos nuestro gestor de logs

load_dotenv()

# Inicializamos el modelo gratuito
llm = ChatGroq(model_name="llama-3.3-70b-versatile", temperature=0)

structured_triage_llm = llm.with_structured_output(TriageRecommendation)
structured_safety_llm = llm.with_structured_output(SafetyCheck)


def triage_agent_node(state: PrescriptionState) -> dict:
    """
    Nodo 1: Analiza síntomas y propone medicamento con Log Criptográfico.
    """
    prompt = f"""
    Eres un Agente Especialista en Triaje Médico.
    Analiza la siguiente información del paciente:
    - Síntomas: {state['patient_symptoms']}
    - Alergias conocidas: {', '.join(state['patient_allergies']) if state['patient_allergies'] else 'Ninguna'}

    Genera una propuesta inicial de tratamiento. Se precavido.
    """
    
    response: TriageRecommendation = structured_triage_llm.invoke(prompt)
    
    # Obtenemos el último log si existe
    last_log = state["audit_logs"][-1] if state.get("audit_logs") else None
    
    # Generamos la entrada del log con Hash encadenado
    log_entry = create_audit_entry(
        previous_log=last_log,
        step="TRIAGE_AGENT",
        action="Proposal Generated",
        details={
            "proposed_medication": response.proposed_medication,
            "dosage": response.dosage,
            "reasoning": response.reasoning
        }
    )
    
    return {
        "proposed_medication": response.proposed_medication,
        "dosage": response.dosage,
        "audit_logs": [log_entry]
    }


def safety_agent_node(state: PrescriptionState) -> dict:
    """
    Nodo 2: Evalúa seguridad y alergias con Log Criptográfico.
    """
    prompt = f"""
    Eres un Agente Farmacéutico de Seguridad Clínica.
    Evalúa si la siguiente propuesta médica es segura para el paciente:

    - Medicamento Propuesto: {state['proposed_medication']}
    - Dosis Propuesta: {state['dosage']}
    - Alergias del Paciente: {', '.join(state['patient_allergies']) if state['patient_allergies'] else 'Ninguna'}
    - Síntomas: {state['patient_symptoms']}

    IMPORTANTE: Si el medicamento propuesto contiene un principio activo relacionado con las alergias del paciente,
    DEBES marcar is_safe como False de forma estricta.
    """

    response: SafetyCheck = structured_safety_llm.invoke(prompt)

    # Obtenemos el último log (que será el generado por el Triaje)
    last_log = state["audit_logs"][-1] if state.get("audit_logs") else None

    # Generamos el nuevo log encadenando el hash del triaje
    log_entry = create_audit_entry(
        previous_log=last_log,
        step="SAFETY_AGENT",
        action="Safety Validation Complete",
        details={
            "is_safe": response.is_safe,
            "risk_notes": response.risk_notes
        }
    )

    return {
        "safety_approved": response.is_safe,
        "risk_notes": response.risk_notes,
        "audit_logs": [log_entry]
    }