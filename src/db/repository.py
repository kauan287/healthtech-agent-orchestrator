"""
Repository layer — isolates all database access from business logic.

Each repository is initialized with an injected SQLAlchemy Session,
making them trivially testable with an in-memory SQLite session.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from src.db.models import AuditLogRecord, PrescriptionRecord

logger = logging.getLogger(__name__)


class PrescriptionRepository:
    """CRUD operations for the prescriptions table."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        prescription_id: str,
        thread_id: str,
        patient_symptoms: str,
        patient_allergies: list,
    ) -> PrescriptionRecord:
        record = PrescriptionRecord(
            prescription_id=prescription_id,
            thread_id=thread_id,
            patient_symptoms=patient_symptoms,
            patient_allergies=patient_allergies,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def get_by_prescription_id(self, prescription_id: str) -> PrescriptionRecord | None:
        return (
            self.session.query(PrescriptionRecord)
            .filter_by(prescription_id=prescription_id)
            .first()
        )

    def update_after_agents(
        self,
        prescription_id: str,
        proposed_medication: str,
        dosage: str,
        safety_approved: bool,
        risk_notes: str,
    ) -> PrescriptionRecord | None:
        record = self.get_by_prescription_id(prescription_id)
        if record:
            record.proposed_medication = proposed_medication
            record.dosage = dosage
            record.safety_approved = safety_approved
            record.risk_notes = risk_notes
            record.updated_at = datetime.now(UTC)
            self.session.flush()
        return record

    def finalize(
        self,
        prescription_id: str,
        human_approved: bool,
        doctor_comments: str,
    ) -> PrescriptionRecord | None:
        record = self.get_by_prescription_id(prescription_id)
        if record:
            record.human_approved = human_approved
            record.doctor_comments = doctor_comments
            record.status = "APPROVED" if human_approved else "REJECTED"
            record.updated_at = datetime.now(UTC)
            self.session.flush()
        return record


class AuditLogRepository:
    """Write operations for the audit_logs table."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def bulk_create(self, prescription_id: str, audit_logs: list[dict]) -> list[AuditLogRecord]:
        """
        Persist a list of audit log dicts.

        Idempotent: entries whose hash already exists are silently skipped,
        which makes this safe to call multiple times for the same logs.
        """
        created: list[AuditLogRecord] = []
        for log in audit_logs:
            existing = self.session.query(AuditLogRecord).filter_by(hash=log["hash"]).first()
            if existing:
                logger.debug("Skipping duplicate audit log hash=%s", log["hash"][:12])
                continue
            record = AuditLogRecord(
                prescription_id=prescription_id,
                step=log["step"],
                action=log["action"],
                timestamp=log["timestamp"],
                details=log["details"],
                previous_hash=log["previous_hash"],
                hash=log["hash"],
            )
            self.session.add(record)
            created.append(record)
        self.session.flush()
        return created

    def get_by_prescription_id(self, prescription_id: str) -> list[AuditLogRecord]:
        return (
            self.session.query(AuditLogRecord)
            .filter_by(prescription_id=prescription_id)
            .order_by(AuditLogRecord.id)
            .all()
        )
