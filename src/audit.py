"""Tamper-evident audit chain.

Each entry carries an HMAC-SHA256 over its own content, the prescription it
belongs to, and the previous entry's MAC. The prescription row stores the last
entry's MAC as the head, sealed with the same key. Editing, reordering, adding,
or removing entries, or moving them to another prescription, breaks verification
for anyone without the key. Details are signed as the exact JSON text stored.
"""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any, NamedTuple, TypedDict

GENESIS_HASH = "0" * 64


class AuditEntry(TypedDict):
    step: str
    action: str
    timestamp: str
    details: str
    previous_hash: str
    hash: str


class ChainCheck(NamedTuple):
    valid: bool
    detail: str


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _mac(key: bytes, *parts: str) -> str:
    return hmac.new(key, canonical_json(parts).encode(), hashlib.sha256).hexdigest()


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def _sign(key: bytes, prescription_id: str, entry: AuditEntry) -> str:
    return _mac(
        key,
        "entry",
        prescription_id,
        entry["step"],
        entry["action"],
        entry["timestamp"],
        entry["details"],
        entry["previous_hash"],
    )


def seal_head(key: bytes, prescription_id: str, head_hash: str) -> str:
    return _mac(key, "head", prescription_id, head_hash)


def new_entry(
    chain: list[AuditEntry],
    *,
    key: bytes,
    prescription_id: str,
    step: str,
    details: dict[str, Any],
) -> AuditEntry:
    entry: AuditEntry = {
        "step": step,
        "action": f"{step} recorded",
        "timestamp": datetime.now(UTC).isoformat(),
        "details": canonical_json(details),
        "previous_hash": chain[-1]["hash"] if chain else GENESIS_HASH,
        "hash": "",
    }
    entry["hash"] = _sign(key, prescription_id, entry)
    return entry


def verify_chain(
    chain: list[AuditEntry], *, key: bytes, prescription_id: str, head_hash: str, seal: str
) -> ChainCheck:
    if not _same(seal, seal_head(key, prescription_id, head_hash)):
        return ChainCheck(False, "The sealed chain head was modified.")
    previous_hash = GENESIS_HASH
    for index, entry in enumerate(chain):
        if entry["previous_hash"] != previous_hash:
            return ChainCheck(
                False, f"Entry {index} ({entry['step']}) does not link to the entry before it."
            )
        if not _same(entry["hash"], _sign(key, prescription_id, entry)):
            return ChainCheck(
                False, f"Entry {index} ({entry['step']}) was modified after it was signed."
            )
        previous_hash = entry["hash"]
    if previous_hash != head_hash:
        return ChainCheck(
            False, "The chain does not end at the sealed head. Entries were added or removed."
        )
    return ChainCheck(True, f"{len(chain)} entries verified.")
