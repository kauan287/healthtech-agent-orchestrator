"""End-to-end check against a running API in demo mode (LLM_PROVIDER=demo).

Usage: python scripts/e2e.py http://localhost:8000 [--restart-cmd "docker compose restart api"]

With --restart-cmd the script restarts the server while a prescription waits for
the doctor, which proves that pending reviews survive a restart. Standard library only.
"""

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from typing import Any


def call(base: str, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        base + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def wait_until_up(base: str, seconds: int = 60) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if call(base, "GET", "/health/ready")[0] == 200:
                return
        except OSError:
            pass
        time.sleep(1)
    raise SystemExit(f"API at {base} did not become ready in {seconds}s")


def expect(label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        raise SystemExit(f"FAIL {label}: expected {expected!r}, got {actual!r}")
    print(f"ok   {label}: {actual!r}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("--restart-cmd")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    wait_until_up(base)

    pid = f"RX-E2E-{uuid.uuid4().hex[:8].upper()}"
    thread = f"session-{pid}"
    intake = {
        "prescription_id": pid,
        "patient_symptoms": "Fiebre de 39 °C y dolor articular",
        "patient_allergies": ["Ibuprofeno"],
    }

    status, rx = call(base, "POST", "/api/v1/prescriptions", intake)
    expect("create status", status, 201)
    expect("create thread_id", rx["thread_id"], thread)
    expect("triage proposal", rx["proposed_medication"], "Paracetamol")
    expect("safety verdict", rx["safety_approved"], True)
    expect("paused for doctor", rx["is_paused_waiting_approval"], True)
    expect("chain valid after agents", rx["audit_chain_valid"], True)

    expect("duplicate create", call(base, "POST", "/api/v1/prescriptions", intake)[0], 409)

    if args.restart_cmd:
        subprocess.run(args.restart_cmd, shell=True, check=True)
        wait_until_up(base)
        print("ok   server restarted while the prescription was paused")

    status, rx = call(base, "GET", f"/api/v1/prescriptions/{thread}")
    expect("still pending after restart", (status, rx["status"]), (200, "PENDING_APPROVAL"))

    decision = {"approved": True, "doctor_comments": "Paracetamol adecuado."}
    status, rx = call(base, "POST", f"/api/v1/prescriptions/{thread}/approve", decision)
    expect("approve status", status, 200)
    expect("approved", (rx["status"], rx["human_approved"]), ("APPROVED", True))
    expect("chain valid after decision", rx["audit_chain_valid"], True)

    reversal = {"approved": False, "doctor_comments": "Cambio de opinión."}
    status, _ = call(base, "POST", f"/api/v1/prescriptions/{thread}/approve", reversal)
    expect("second decision rejected", status, 409)

    status, trail = call(base, "GET", f"/api/v1/prescriptions/{thread}/audit")
    expect("audit trail valid", (status, trail["valid"]), (200, True))
    expect(
        "audit steps",
        [entry["step"] for entry in trail["entries"]],
        ["TRIAGE_AGENT", "SAFETY_AGENT", "HUMAN_APPROVAL"],
    )

    allergic = {**intake, "prescription_id": pid + "-ALG", "patient_allergies": ["Paracetamol"]}
    status, rx = call(base, "POST", "/api/v1/prescriptions", allergic)
    expect("allergy conflict flagged", (status, rx["safety_approved"]), (201, False))

    expect("unknown prescription", call(base, "GET", "/api/v1/prescriptions/session-nope")[0], 404)
    print(f"PASS e2e ({pid})")


if __name__ == "__main__":
    main()
