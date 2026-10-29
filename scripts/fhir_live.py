"""Manual check of the FHIR integration against a real FHIR R4 server.

Usage: python scripts/fhir_live.py http://localhost:8000 [--fhir-base https://hapi.fhir.org/baseR4]

The orchestrator must run in demo mode with FHIR_BASE_URL set to the same FHIR
server. The script creates a synthetic Patient and an active Paracetamol
allergy, tagged urn:healthtech:synthetic|demo, runs from-fhir with fever
symptoms, exports the FHIR Bundle, and deletes what it created. Standard library
and httpx only.
"""

import argparse
import base64
import time
import uuid
from typing import Any

import httpx

FHIR_JSON = "application/fhir+json"
SYNTHETIC_TAG = {"system": "urn:healthtech:synthetic", "code": "demo"}


def expect(label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        raise SystemExit(f"FAIL {label}: expected {expected!r}, got {actual!r}")
    print(f"ok   {label}: {actual!r}")


def wait_until_up(api: httpx.Client, seconds: int = 60) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if api.get("/health/ready").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise SystemExit(f"API at {api.base_url} did not become ready in {seconds}s")


def create(fhir: httpx.Client, resource: dict[str, Any]) -> str:
    response = fhir.post(resource["resourceType"], json=resource)
    if response.status_code != 201:
        raise SystemExit(f"FAIL create {resource['resourceType']}: {response.text[:500]}")
    reference = f"{resource['resourceType']}/{response.json()['id']}"
    print(f"ok   created synthetic {reference}")
    return reference


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("--fhir-base", default="https://hapi.fhir.org/baseR4")
    args = parser.parse_args()
    fhir = httpx.Client(
        base_url=args.fhir_base.rstrip("/") + "/",
        headers={"Accept": FHIR_JSON, "Content-Type": FHIR_JSON},
        timeout=30,
    )
    api = httpx.Client(base_url=args.base_url.rstrip("/"), timeout=60)
    wait_until_up(api)

    patient = create(
        fhir,
        {
            "resourceType": "Patient",
            "meta": {"tag": [SYNTHETIC_TAG]},
            "name": [{"family": "Test", "given": ["Synthetic"], "text": "Synthetic Test"}],
        },
    )
    allergy = create(
        fhir,
        {
            "resourceType": "AllergyIntolerance",
            "meta": {"tag": [SYNTHETIC_TAG]},
            "clinicalStatus": {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                        "code": "active",
                    }
                ]
            },
            "code": {"text": "Paracetamol"},
            "patient": {"reference": patient},
        },
    )
    try:
        pid = f"RX-FHIR-{uuid.uuid4().hex[:8].upper()}"
        response = api.post(
            "/api/v1/prescriptions/from-fhir",
            json={
                "prescription_id": pid,
                "fhir_patient_id": patient.removeprefix("Patient/"),
                "patient_symptoms": "Fiebre de 39 °C y dolor articular",
            },
        )
        expect("from-fhir status", response.status_code, 201)
        rx = response.json()
        expect("allergy read from FHIR", rx["patient_allergies"], ["Paracetamol"])
        expect("signed FHIR patient", rx["fhir_patient"], patient)
        expect("triage proposal", rx["proposed_medication"], "Paracetamol")
        expect("safety flagged the allergy", rx["safety_approved"], False)

        response = api.get(f"/api/v1/prescriptions/{rx['thread_id']}/fhir")
        expect("export status", response.status_code, 200)
        expect("export media type", response.headers["content-type"], FHIR_JSON)
        bundle = response.json()
        expect("bundle type", (bundle["resourceType"], bundle["type"]), ("Bundle", "collection"))
        request, provenance = (entry["resource"] for entry in bundle["entry"])
        expect("request subject", request["subject"]["reference"], patient)
        expect("request status", (request["status"], request["intent"]), ("draft", "proposal"))
        expect(
            "provenance target", provenance["target"][0]["reference"], bundle["entry"][0]["fullUrl"]
        )
        mac = base64.b64decode(provenance["signature"][0]["data"], validate=True)
        expect("signature carries a 32-byte MAC", len(mac), 32)
        print(f"PASS fhir live ({pid}, {patient})")
    finally:
        for reference in (allergy, patient):
            status = fhir.delete(reference).status_code
            print(f"{'ok  ' if status < 300 else 'note'} delete {reference}: HTTP {status}")


if __name__ == "__main__":
    main()
