"""
Unit tests for the cryptographic audit chain (src/audit.py).

These tests are pure Python — no LLM, no database, no network.
They verify the SHA-256 hash-chaining logic that makes the audit
trail tamper-evident.
"""
import pytest

from src.audit import calculate_log_hash, create_audit_entry, verify_audit_chain


# ── Helpers ───────────────────────────────────────────────────────────────────

def build_chain(n: int) -> list[dict]:
    """Build a valid audit chain of length *n*."""
    logs: list[dict] = []
    steps = ["TRIAGE_AGENT", "SAFETY_AGENT", "HUMAN_APPROVAL"]
    for i in range(n):
        entry = create_audit_entry(
            previous_log=logs[-1] if logs else None,
            step=steps[i % len(steps)],
            action=f"Action {i}",
            details={"index": i},
        )
        logs.append(entry)
    return logs


# ── Unit tests ────────────────────────────────────────────────────────────────

class TestCreateAuditEntry:
    def test_genesis_entry_uses_genesis_hash(self):
        entry = create_audit_entry(None, "TRIAGE_AGENT", "Start", {"x": 1})
        assert entry["previous_hash"].startswith("GENESIS_HASH")

    def test_entry_contains_required_keys(self):
        entry = create_audit_entry(None, "TRIAGE_AGENT", "Start", {})
        for key in ("step", "action", "timestamp", "details", "previous_hash", "hash"):
            assert key in entry, f"Missing key: {key}"

    def test_chained_entry_references_previous_hash(self):
        first = create_audit_entry(None, "TRIAGE_AGENT", "A", {})
        second = create_audit_entry(first, "SAFETY_AGENT", "B", {})
        assert second["previous_hash"] == first["hash"]

    def test_different_details_produce_different_hashes(self):
        e1 = create_audit_entry(None, "TRIAGE_AGENT", "A", {"med": "Paracetamol"})
        e2 = create_audit_entry(None, "TRIAGE_AGENT", "A", {"med": "Ibuprofeno"})
        assert e1["hash"] != e2["hash"]

    def test_hash_is_deterministic_for_same_inputs(self):
        """Same inputs → same hash (given same timestamp)."""
        ts = "2024-01-01T00:00:00+00:00"
        h1 = calculate_log_hash("GENESIS", "STEP", "ACTION", {"k": "v"}, ts)
        h2 = calculate_log_hash("GENESIS", "STEP", "ACTION", {"k": "v"}, ts)
        assert h1 == h2

    def test_hash_length_is_64_chars(self):
        entry = create_audit_entry(None, "TRIAGE_AGENT", "A", {})
        assert len(entry["hash"]) == 64  # SHA-256 hex digest


class TestVerifyAuditChain:
    def test_empty_chain_is_valid(self):
        valid, msg = verify_audit_chain([])
        assert valid is True

    def test_single_entry_chain_is_valid(self):
        logs = build_chain(1)
        valid, _ = verify_audit_chain(logs)
        assert valid is True

    def test_multi_entry_chain_is_valid(self):
        logs = build_chain(3)
        valid, msg = verify_audit_chain(logs)
        assert valid is True
        assert "íntegra" in msg or "OK" in msg.upper() or "100%" in msg

    def test_tampered_details_invalidates_chain(self):
        logs = build_chain(2)
        # Simulate a DB-level modification — change the medication name
        logs[0]["details"]["medication"] = "MORPHINE_100MG_TAMPERED"
        valid, msg = verify_audit_chain(logs)
        assert valid is False
        assert "CORRUPCIÓN" in msg or "alterado" in msg.lower() or "corrupt" in msg.lower()

    def test_broken_hash_link_invalidates_chain(self):
        logs = build_chain(2)
        # Sever the link between entries
        logs[1]["previous_hash"] = "00000000000000000000000000000000invalid"
        valid, msg = verify_audit_chain(logs)
        assert valid is False

    def test_tampered_hash_field_invalidates_chain(self):
        logs = build_chain(1)
        logs[0]["hash"] = "a" * 64  # Replace hash with garbage
        valid, _ = verify_audit_chain(logs)
        assert valid is False

    @pytest.mark.parametrize("n", [1, 2, 5, 10])
    def test_valid_chain_of_various_lengths(self, n: int):
        logs = build_chain(n)
        valid, _ = verify_audit_chain(logs)
        assert valid is True
