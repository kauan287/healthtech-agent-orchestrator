"""
SQLAlchemy ORM models for persistent storage.

Two tables are used:
  - prescriptions: one row per prescription workflow
  - audit_logs:    one row per audit chain entry (immutable once written)
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PrescriptionRecord(Base):
    """Stores the high-level prescription state for each workflow run."""

    __tablename__ = "prescriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prescription_id: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    thread_id: Mapped[str] = mapped_column(String(200), unique=True, index=True, nullable=False)
    patient_symptoms: Mapped[str] = mapped_column(Text, nullable=False)
    patient_allergies: Mapped[list] = mapped_column(JSON, default=list)
    proposed_medication: Mapped[str | None] = mapped_column(String(500), nullable=True)
    dosage: Mapped[str | None] = mapped_column(String(200), nullable=True)
    safety_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    human_approved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    doctor_comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    # PENDING_APPROVAL → APPROVED | REJECTED
    status: Mapped[str] = mapped_column(String(50), default="PENDING_APPROVAL")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:
        return f"<PrescriptionRecord id={self.prescription_id} status={self.status}>"


class AuditLogRecord(Base):
    """
    Persists each entry of the in-memory cryptographic audit chain.

    The hash column is unique: the application-level chain guarantees
    no two entries ever produce the same SHA-256 hash, so this also
    acts as a natural deduplication key for idempotent inserts.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prescription_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    step: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(200), nullable=False)
    timestamp: Mapped[str] = mapped_column(String(50), nullable=False)  # ISO-8601
    details: Mapped[dict] = mapped_column(JSON, nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    hash: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:
        return f"<AuditLogRecord step={self.step} hash={self.hash[:12]}...>"
