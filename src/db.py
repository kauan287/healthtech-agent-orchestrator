from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, DateTime, ForeignKey, Integer, String, Text, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from src.audit import AuditEntry


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class PrescriptionRecord(Base):
    __tablename__ = "prescriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prescription_id: Mapped[str] = mapped_column(String(100), unique=True)
    head_hash: Mapped[str] = mapped_column(String(64))
    seal: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class AuditLogRecord(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prescription_id: Mapped[str] = mapped_column(
        ForeignKey("prescriptions.prescription_id"), index=True
    )
    step: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(200))
    timestamp: Mapped[str] = mapped_column(String(50))
    details: Mapped[str] = mapped_column(Text)
    previous_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)

    def to_entry(self) -> AuditEntry:
        return {
            "step": self.step,
            "action": self.action,
            "timestamp": self.timestamp,
            "details": self.details,
            "previous_hash": self.previous_hash,
            "hash": self.hash,
        }


@dataclass(frozen=True)
class StoredChain:
    head_hash: str
    seal: str
    chain: list[AuditEntry]


class PrescriptionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def head(self, prescription_id: str) -> tuple[str, str] | None:
        row = self.session.execute(
            select(PrescriptionRecord.head_hash, PrescriptionRecord.seal).where(
                PrescriptionRecord.prescription_id == prescription_id
            )
        ).one_or_none()
        return (row.head_hash, row.seal) if row else None

    def snapshot(self, prescription_id: str) -> StoredChain | None:
        """Read the head and the chain as one consistent state, or None if unknown.

        A decision moves the head and adds its entry in one commit. If the head is
        the same before and after the chain is read, both come from the same state,
        whatever the isolation level.
        """
        head = self.head(prescription_id)
        while head is not None:
            chain = self.chain(prescription_id)
            after = self.head(prescription_id)
            if after == head:
                return StoredChain(head_hash=head[0], seal=head[1], chain=chain)
            head = after
        return None

    def chain(self, prescription_id: str) -> list[AuditEntry]:
        rows = self.session.scalars(
            select(AuditLogRecord)
            .where(AuditLogRecord.prescription_id == prescription_id)
            .order_by(AuditLogRecord.id)
        )
        return [row.to_entry() for row in rows]

    def add(self, prescription_id: str, chain: list[AuditEntry], seal: str) -> None:
        self.session.add(
            PrescriptionRecord(
                prescription_id=prescription_id, head_hash=chain[-1]["hash"], seal=seal
            )
        )
        self.session.flush()
        self._insert(prescription_id, chain)

    def append(
        self, prescription_id: str, expected_head: str, entry: AuditEntry, seal: str
    ) -> bool:
        """Append `entry` if the head is still `expected_head`. False if another write won.

        The conditional UPDATE locks the row on Postgres and the database on SQLite,
        so of two concurrent appends to the same head exactly one succeeds.
        """
        result = self.session.execute(
            update(PrescriptionRecord)
            .where(
                PrescriptionRecord.prescription_id == prescription_id,
                PrescriptionRecord.head_hash == expected_head,
            )
            .values(head_hash=entry["hash"], seal=seal)
        )
        if cast(CursorResult[Any], result).rowcount != 1:
            return False
        self._insert(prescription_id, [entry])
        return True

    def _insert(self, prescription_id: str, entries: list[AuditEntry]) -> None:
        self.session.add_all(
            AuditLogRecord(prescription_id=prescription_id, **entry) for entry in entries
        )
        self.session.flush()
