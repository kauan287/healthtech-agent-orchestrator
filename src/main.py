from dotenv import load_dotenv
from src.state import PrescriptionState
from src.graph import create_app
from src.audit import create_audit_entry, verify_audit_chain

load_dotenv()

# Instanciamos la app desde nuestra función encapsulada
app = create_app(interrupt_after=["safety_agent"])

if __name__ == "__main__":
    config = {"configurable": {"thread_id": "session-rx-2002"}}

    initial_input: PrescriptionState = {
        "prescription_id": "RX-2002",
        "patient_symptoms": "Fiebre alta de 39C y dolor articular.",
        "patient_allergies": ["Ibuprofeno"],
        "proposed_medication": None,
        "dosage": None,
        "safety_approved": False,
        "risk_notes": None,
        "human_approved": None,
        "doctor_comments": None,
        "audit_logs": []
    }

    print("=== 🚀 EJECUTANDO ORQUESTACIÓN CON AUDITORÍA INMUTABLE ===")
    
    # 1. Correr los agentes hasta la interrupción HITL
    for event in app.stream(initial_input, config):
        pass

    current_state = app.get_state(config)
    print("\n⏸️ SISTEMA PAUSADO PARA VALIDACIÓN MÉDICA")
    print(f"Propuesta: {current_state.values.get('proposed_medication')} ({current_state.values.get('dosage')})")
    print(f"Aprobado por Agente de Seguridad: {current_state.values.get('safety_approved')}")
    print(f"Riesgos: {current_state.values.get('risk_notes')}")

    # 2. Intervención Humana
    print("\n--- 👨‍⚕️ DECISIÓN DEL MÉDICO ---")
    decision = input("¿Aprobar receta? (s/n): ").strip().lower()
    comments = input("Observaciones clínicas: ").strip()
    is_approved = (decision == 's')

    # 3. Encadenar el Log del Humano con el hash del último agente
    existing_logs = current_state.values.get("audit_logs", [])
    last_log = existing_logs[-1] if existing_logs else None

    human_log_entry = create_audit_entry(
        previous_log=last_log,
        step="HUMAN_APPROVAL",
        action="Doctor Decision Recorded",
        details={
            "approved": is_approved,
            "comments": comments
        }
    )

    # Actualizamos el estado con la decisión y el nuevo log firmado
    app.update_state(
        config,
        {
            "human_approved": is_approved,
            "doctor_comments": comments,
            "audit_logs": [human_log_entry]
        },
        as_node="safety_agent"
    )

    # 4. Finalizar flujo
    for event in app.stream(None, config):
        pass

    # 5. VERIFICACIÓN FINAL DE INTEGRIDAD DE LA CADENA
    final_state = app.get_state(config).values
    logs_chain = final_state.get("audit_logs", [])

    print("\n" + "="*60)
    print("🔒 AUDITORÍA CRIPTOGRÁFICA DE LA SESIÓN")
    print("="*60)
    for idx, log in enumerate(logs_chain):
        print(f"\n[Bloque {idx} - {log['step']}]")
        print(f"  Acción:        {log['action']}")
        print(f"  Prev Hash:     {log['previous_hash'][:16]}...")
        print(f"  Hash Actual:   {log['hash'][:16]}...")

    # Verificación automática
    is_valid, msg = verify_audit_chain(logs_chain)
    print("\n" + "-"*60)
    print(f"RESULTADO DE INTEGRIDAD: {'✅ EN ORDEN' if is_valid else '❌ CORRUPTO'}")
    print(f"Detalle: {msg}")
    print("-"*60)