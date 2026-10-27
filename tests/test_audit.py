from collections.abc import Callable
from typing import Any

import pytest

from src.audit import GENESIS_HASH, AuditEntry, ChainCheck, new_entry, seal_head, verify_chain

KEY = b"unit-test-key"


def build_chain(prescription_id: str = "RX-1", key: bytes = KEY) -> list[AuditEntry]:
    chain: list[AuditEntry] = []
    steps: list[tuple[str, dict[str, Any]]] = [
        ("TRIAGE_AGENT", {"proposed_medication": "Paracetamol"}),
        ("SAFETY_AGENT", {"is_safe": True}),
        ("HUMAN_APPROVAL", {"approved": False}),
    ]
    for step, details in steps:
        chain.append(
            new_entry(chain, key=key, prescription_id=prescription_id, step=step, details=details)
        )
    return chain


def verify(chain: list[AuditEntry], head: str | None = None, seal: str | None = None) -> ChainCheck:
    head = head if head is not None else build_head(chain)
    seal = seal if seal is not None else seal_head(KEY, "RX-1", head)
    return verify_chain(chain, key=KEY, prescription_id="RX-1", head_hash=head, seal=seal)


def build_head(chain: list[AuditEntry]) -> str:
    return chain[-1]["hash"] if chain else GENESIS_HASH


def test_intact_chain_verifies() -> None:
    chain = build_chain()
    assert chain[0]["previous_hash"] == GENESIS_HASH
    assert chain[2]["previous_hash"] == chain[1]["hash"]
    assert chain[0]["details"] == '{"proposed_medication":"Paracetamol"}'
    assert verify(chain) == (True, "3 entries verified.")


def test_edited_details_are_detected() -> None:
    chain = build_chain()
    chain[0]["details"] = '{"proposed_medication":"Morfina 100 mg"}'
    assert verify(chain) == (False, "Entry 0 (TRIAGE_AGENT) was modified after it was signed.")


def test_chain_recomputed_without_the_key_is_detected() -> None:
    forged = build_chain(key=b"attacker-guess")
    assert verify(forged) == (False, "Entry 0 (TRIAGE_AGENT) was modified after it was signed.")


def test_non_hex_hash_is_reported_not_raised() -> None:
    chain = build_chain()
    head = chain[-1]["hash"]
    chain[0]["hash"] = "é"
    assert verify(chain, head=head).valid is False


def test_chain_moved_to_another_prescription_is_detected() -> None:
    assert verify(build_chain(prescription_id="RX-OTHER")).valid is False


def test_truncated_tail_is_detected_by_the_sealed_head() -> None:
    chain = build_chain()
    head = chain[-1]["hash"]
    assert verify(chain[:2], head=head) == (
        False,
        "The chain does not end at the sealed head. Entries were added or removed.",
    )


def test_head_rewound_without_the_key_is_detected() -> None:
    chain = build_chain()[:2]
    assert verify(chain, seal="0" * 64) == (False, "The sealed chain head was modified.")


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda c: c.pop(1), "Entry 1 (HUMAN_APPROVAL) does not link to the entry before it."),
        (lambda c: c.reverse(), "Entry 0 (HUMAN_APPROVAL) does not link to the entry before it."),
    ],
)
def test_deleted_or_reordered_entries_are_detected(
    mutate: Callable[[list[AuditEntry]], object], reason: str
) -> None:
    chain = build_chain()
    head = chain[-1]["hash"]
    mutate(chain)
    assert verify(chain, head=head) == (False, reason)
