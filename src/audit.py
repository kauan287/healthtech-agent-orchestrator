import hashlib
import json
from datetime import UTC, datetime
from typing import Any


def calculate_log_hash(
    previous_hash: str, step: str, action: str, details: dict[str, Any], timestamp: str
) -> str:
    """
    Calcula un hash SHA-256 único uniendo el hash anterior con la entrada actual.
    """
    # Ordenamos el diccionario details para garantizar la consistencia en el hash
    serialized_details = json.dumps(details, sort_keys=True)

    payload = f"{previous_hash}|{step}|{action}|{serialized_details}|{timestamp}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_audit_entry(
    previous_log: dict[str, Any] | None, step: str, action: str, details: dict[str, Any]
) -> dict[str, Any]:
    """
    Crea un registro de auditoría con su hash criptográfico encadenado.
    """
    previous_hash = (
        previous_log["hash"]
        if previous_log and "hash" in previous_log
        else "GENESIS_HASH_00000000000000000000000000000000"
    )
    timestamp = datetime.now(UTC).isoformat()

    current_hash = calculate_log_hash(
        previous_hash=previous_hash, step=step, action=action, details=details, timestamp=timestamp
    )

    return {
        "step": step,
        "action": action,
        "timestamp": timestamp,
        "details": details,
        "previous_hash": previous_hash,
        "hash": current_hash,
    }


def verify_audit_chain(logs: list[dict[str, Any]]) -> tuple[bool, str]:
    """
    Verifica la integridad de toda la cadena de auditoría.
    Retorna (True, "OK") o (False, "Motivo del fallo").
    """
    if not logs:
        return True, "Cadena vacía"

    for i, log in enumerate(logs):
        expected_prev_hash = (
            "GENESIS_HASH_00000000000000000000000000000000" if i == 0 else logs[i - 1]["hash"]
        )

        # 1. Validar que la referencia al hash anterior sea exacta
        if log["previous_hash"] != expected_prev_hash:
            return (
                False,
                f"Error de enlace en el registro {i} ({log['step']}): el hash previo no coincide.",
            )

        # 2. Recalcular el hash del registro actual para comprobar si fue alterado
        recalculated_hash = calculate_log_hash(
            previous_hash=log["previous_hash"],
            step=log["step"],
            action=log["action"],
            details=log["details"],
            timestamp=log["timestamp"],
        )

        if log["hash"] != recalculated_hash:
            return (
                False,
                f"⚠️ ¡CORRUPCIÓN DETECTADA! El registro {i} ({log['step']}) ha sido alterado manualmente.",
            )

    return True, "Cadena de auditoría 100% íntegra y verificada."
