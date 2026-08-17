from src.audit import create_audit_entry, verify_audit_chain

if __name__ == "__main__":
    logs = []

    # 1. Crear Primer Registro (Triaje)
    entry1 = create_audit_entry(
        previous_log=None,
        step="TRIAGE_AGENT",
        action="Proposal Generated",
        details={"medication": "Paracetamol", "dosage": "500mg"}
    )
    logs.append(entry1)

    # 2. Crear Segundo Registro (Seguridad)
    entry2 = create_audit_entry(
        previous_log=logs[-1],
        step="SAFETY_AGENT",
        action="Safety Validation",
        details={"is_safe": True, "risk_notes": "Sin alergias"}
    )
    logs.append(entry2)

    # 3. Verificación de Cadena Normal
    is_valid, msg = verify_audit_chain(logs)
    print("--- Verificación Inicial ---")
    print(f"¿Válida?: {is_valid} | Mensaje: {msg}")

    # 4. SIMULACIÓN DE ATAQUE / ALTERACIÓN DE DATOS (Hackeo de Base de Datos)
    print("\n--- Simulando alteración maliciosa en el registro 0 ---")
    logs[0]["details"]["medication"] = "Morfina 100mg" # Alguien cambió el medicamento a espaldas del sistema

    # 5. Volver a verificar la cadena
    is_valid, msg = verify_audit_chain(logs)
    print("--- Verificación Tras Alteración ---")
    print(f"¿Válida?: {is_valid} | Mensaje: {msg}")