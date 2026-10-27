from typing import Any

import pytest

from src.audit import AuditEntry, new_entry, seal_head
from src.prescription import Prescription, Status, read_prescription

KEY = b"unit-test-key"
TRIAGE = {
    "proposed_medication": "Paracetamol",
    "dosage": "500 mg",
    "reasoning": "r",
    "urgency_level": "BAJO",
}
SAFETY = {"is_safe": True, "risk_notes": "ok"}


def signed(*steps: tuple[str, dict[str, Any]]) -> Prescription:
    """Read a chain that the key holder signed and sealed, so only its content can be wrong."""
    chain: list[AuditEntry] = []
    for step, details in steps:
        chain.append(new_entry(chain, key=KEY, prescription_id="RX-1", step=step, details=details))
    head = chain[-1]["hash"]
    return read_prescription(
        chain, key=KEY, prescription_id="RX-1", head_hash=head, seal=seal_head(KEY, "RX-1", head)
    )


def test_decided_chain_reads_every_fact() -> None:
    rx = signed(
        ("TRIAGE_AGENT", TRIAGE),
        ("SAFETY_AGENT", SAFETY),
        ("HUMAN_APPROVAL", {"approved": True, "comments": "ok"}),
    )
    assert rx.status is Status.APPROVED
    assert (rx.triage and rx.triage.proposed_medication, rx.safety and rx.safety.is_safe) == (
        "Paracetamol",
        True,
    )


@pytest.mark.parametrize(
    ("steps", "detail"),
    [
        ((("TRIAGE_AGENT", TRIAGE),), "Unexpected audit steps: ['TRIAGE_AGENT']."),
        (
            (("TRIAGE_AGENT", TRIAGE), ("HUMAN_APPROVAL", {"approved": True, "comments": ""})),
            "Unexpected audit steps: ['TRIAGE_AGENT', 'HUMAN_APPROVAL'].",
        ),
    ],
)
def test_signed_chain_with_unexpected_steps_is_invalid(
    steps: tuple[tuple[str, dict[str, Any]], ...], detail: str
) -> None:
    rx = signed(*steps)
    assert (rx.status, rx.audit_detail, rx.triage) == (Status.INVALID, detail, None)


def test_signed_details_with_the_wrong_schema_are_invalid() -> None:
    rx = signed(
        ("TRIAGE_AGENT", {"proposed_medication": "Paracetamol"}),
        ("SAFETY_AGENT", SAFETY),
    )
    assert rx.status is Status.INVALID
    assert rx.audit_detail.startswith("Signed details do not match the expected schema")


def test_decision_must_be_a_real_boolean() -> None:
    rx = signed(
        ("TRIAGE_AGENT", TRIAGE),
        ("SAFETY_AGENT", SAFETY),
        ("HUMAN_APPROVAL", {"approved": "yes", "comments": ""}),
    )
    assert rx.status is Status.INVALID
