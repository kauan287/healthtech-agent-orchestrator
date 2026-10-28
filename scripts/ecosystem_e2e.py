"""End-to-end check across this service and SmartDispenser Core.

Usage:
    uv run --with websockets python scripts/ecosystem_e2e.py \
        http://localhost:8000 http://localhost:5000 <webhook-secret> <orchestrator-sqlite-path>

The orchestrator must run with LLM_PROVIDER=demo on the SQLite file given, and
SmartDispenser must use it as HealthTechApi:BaseUrl. The script plays a vending
machine over SignalR, approves and rejects prescriptions, pays through the signed
webhook, and edits the orchestrator database the way an attacker would.
"""

import asyncio
import hashlib
import hmac
import json
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid
from typing import Any

import websockets

RECORD_SEPARATOR = "\x1e"


class Ecosystem:
    def __init__(self, orchestrator: str, dispenser: str, secret: str, db_path: str) -> None:
        self.orchestrator = orchestrator.rstrip("/")
        self.dispenser = dispenser.rstrip("/")
        self.secret = secret.encode()
        self.db_path = db_path

    def call(
        self, base: str, method: str, path: str, body: Any = None, **headers: str
    ) -> tuple[int, Any]:
        data = (
            body
            if isinstance(body, bytes)
            else json.dumps(body).encode()
            if body is not None
            else None
        )
        request = urllib.request.Request(
            base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", **headers},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                text = response.read()
                return response.status, json.loads(text) if text else None
        except urllib.error.HTTPError as error:
            text = error.read()
            return error.code, json.loads(text) if text else None

    def prescription(self, approved: bool) -> str:
        pid = f"RX-ECO-{uuid.uuid4().hex[:6].upper()}"
        intake = {
            "prescription_id": pid,
            "patient_symptoms": "Fiebre de 39 °C",
            "patient_allergies": [],
        }
        assert self.call(self.orchestrator, "POST", "/api/v1/prescriptions", intake)[0] == 201
        decision = {"approved": approved, "doctor_comments": "Revisado."}
        assert (
            self.call(
                self.orchestrator, "POST", f"/api/v1/prescriptions/session-{pid}/approve", decision
            )[0]
            == 200
        )
        return pid

    def dispense(self, machine_id: str, pid: str) -> tuple[int, Any]:
        body = {"prescriptionId": pid, "patientIdentifier": "PATIENT-1"}
        return self.call(self.dispenser, "POST", f"/api/dispensers/{machine_id}/dispense", body)

    def pay(self, request_id: str) -> Any:
        body = json.dumps(
            {
                "transactionId": f"TX-{request_id}",
                "idempotencyKey": f"idem-{request_id}",
                "dispenseRequestId": request_id,
                "amount": 4.5,
                "currency": "EUR",
                "status": "confirmed",
                "provider": "sandbox",
                "timestamp": "2026-10-28T09:00:00Z",
            }
        ).encode()
        signature = hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        return self.call(
            self.dispenser,
            "POST",
            "/api/payments/webhook",
            body,
            **{"X-Signature-SHA256": f"sha256={signature}"},
        )[1]

    def tamper(self, pid: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE audit_logs SET details = replace(details, 'Paracetamol', 'Morfina') "
                "WHERE prescription_id = ? AND step = 'TRIAGE_AGENT'",
                (pid,),
            )


def expect(label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        raise SystemExit(f"FAIL {label}: expected {expected!r}, got {actual!r}")
    print(f"ok   {label}: {actual!r}")


async def machine(url: str, commands: asyncio.Queue[Any]) -> None:
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"protocol": "json", "version": 1}) + RECORD_SEPARATOR)
        await ws.recv()
        report = {"type": 1, "invocationId": "0", "target": "ReportInventory", "arguments": [10]}
        await ws.send(json.dumps(report) + RECORD_SEPARATOR)
        async for frame in ws:
            for message in filter(None, str(frame).split(RECORD_SEPARATOR)):
                parsed = json.loads(message)
                if parsed.get("type") == 1 and parsed["target"] == "ReceiveDispenseCommand":
                    command = parsed["arguments"][0]
                    await commands.put(command)
                    confirm = {
                        "type": 1,
                        "invocationId": "1",
                        "target": "ConfirmDispense",
                        "arguments": [command["requestId"]],
                    }
                    await ws.send(json.dumps(confirm) + RECORD_SEPARATOR)


async def main() -> None:
    eco = Ecosystem(*sys.argv[1:5])
    machine_spec = {
        "name": "Lobby-E2E",
        "location": "E2E",
        "inventoryCapacity": 20,
        "medication": "Paracetamol",
    }
    machine_id = eco.call(eco.dispenser, "POST", "/api/dispensers", machine_spec)[1]["id"]
    commands: asyncio.Queue[Any] = asyncio.Queue()
    hub = (
        eco.dispenser.replace("http", "ws", 1)
        + f"/hubs/dispensers?machineId={machine_id}&firmwareVersion=2.4.1"
    )
    simulator = asyncio.create_task(machine(hub, commands))
    for _ in range(50):
        state = eco.call(eco.dispenser, "GET", f"/api/dispensers/{machine_id}")[1]
        if (state["status"], state["inventoryCount"]) == ("Idle", 10):
            break
        await asyncio.sleep(0.2)
    expect("machine online with stock", (state["status"], state["inventoryCount"]), ("Idle", 10))

    pending = f"RX-ECO-{uuid.uuid4().hex[:6].upper()}"
    eco.call(
        eco.orchestrator,
        "POST",
        "/api/v1/prescriptions",
        {"prescription_id": pending, "patient_symptoms": "fiebre", "patient_allergies": []},
    )
    status, body = eco.dispense(machine_id, pending)
    expect(
        "pending doctor review is refused",
        (status, body["detail"]),
        (422, "Prescription is pending doctor approval."),
    )

    status, body = eco.dispense(machine_id, eco.prescription(approved=True))
    expect("approved prescription is reserved", status, 202)
    request_id = body["dispenseRequestId"]
    expect(
        "reservation waits for payment",
        eco.call(eco.dispenser, "GET", f"/api/dispense-requests/{request_id}")[1]["status"],
        "PendingPayment",
    )
    expect("signed payment accepted", eco.pay(request_id), {"received": True, "duplicate": False})
    command = await asyncio.wait_for(commands.get(), 10)
    expect(
        "machine told what to release",
        (command["requestId"], command["medication"]),
        (request_id, "Paracetamol"),
    )
    for _ in range(50):
        final = eco.call(eco.dispenser, "GET", f"/api/dispense-requests/{request_id}")[1]["status"]
        if final == "Completed":
            break
        await asyncio.sleep(0.2)
    expect("dispense completed", final, "Completed")

    status, body = eco.dispense(machine_id, eco.prescription(approved=False))
    expect(
        "rejected prescription is refused",
        (status, body["detail"]),
        (422, "Doctor has not approved this prescription."),
    )

    tampered = eco.prescription(approved=True)
    eco.tamper(tampered)
    status, body = eco.dispense(machine_id, tampered)
    expect(
        "tampered prescription is refused",
        (status, body["detail"]),
        (422, "Audit chain integrity check failed: possible data tampering."),
    )
    simulator.cancel()
    print("PASS ecosystem e2e")


if __name__ == "__main__":
    asyncio.run(main())
