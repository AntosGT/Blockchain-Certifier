import unittest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from db_helper import PostgresTestCase
from src.core.audit_logger import AuditLoggerManager
from src.core.evidence_manager import EvidenceManager
from src.core.state_machine import EvidenceState

HASH_A = "a" * 64
HASH_B = "b" * 64

class TestPersistence(PostgresTestCase):

    def setUp(self):
        super().setUp()
        self.audit = AuditLoggerManager(self.engine)
        self.manager = EvidenceManager(self.engine, self.audit)
        self.manager.create_evidence("EV-1", self.user.id, "Doc", "texto")

    def _walk_to_verified(self, evidence_id):
        for st in (EvidenceState.HASHED, EvidenceState.SUBMITTED, EvidenceState.PENDING_BITCOIN,
                   EvidenceState.CONFIRMED, EvidenceState.VERIFIED):
            self.manager.update_evidence_status(evidence_id, st)

    def test_data_survives_new_manager_instances(self):
        """Data lives in Postgres, not in the instance: a new manager sees it."""
        self.manager.update_evidence_status("EV-1", EvidenceState.HASHED)
        other = EvidenceManager(self.engine, AuditLoggerManager(self.engine))
        self.assertEqual(other.get_evidence("EV-1").status, EvidenceState.HASHED)
        self.assertEqual([e.event_type for e in other.audit_logger.get_events_for_evidence("EV-1")],
                         ["EVIDENCE_CREATED", "STATUS_CHANGED"])

    def test_audit_log_blocked_at_database_level(self):
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as conn:
                conn.execute(text("UPDATE audit_events SET actor = 'hacker'"))
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as conn:
                conn.execute(text("DELETE FROM audit_events"))
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as conn:
                conn.execute(text("TRUNCATE audit_events"))
        self.assertEqual(len(self.audit.get_events_for_evidence("EV-1")), 1)

    def test_manifest_hash_is_write_once(self):
        self.manager.set_manifest_hash("EV-1", HASH_A)
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as conn:
                conn.execute(text("UPDATE evidences SET manifest_hash = :h WHERE id = 'EV-1'"), {"h": HASH_B})
        with self.assertRaises(DBAPIError):
            self.manager.set_manifest_hash("EV-1", HASH_B)
        self.assertEqual(self.manager.get_evidence("EV-1").manifest_hash, HASH_A)

    def test_verified_evidence_is_immutable_in_database(self):
        self._walk_to_verified("EV-1")
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as conn:
                conn.execute(text("UPDATE evidences SET title = 'otro' WHERE id = 'EV-1'"))
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as conn:
                conn.execute(text("DELETE FROM evidences WHERE id = 'EV-1'"))

    def test_files_and_proof_manifest_cannot_be_edited(self):
        meta = {"filename": "a.txt", "mime": "text/plain", "size": 3, "sha256": HASH_A,
                "salt_hex": "c" * 64, "storage_reference": "EV-1/a.txt"}
        self.manager.add_file("EV-1", meta)
        self.manager.save_proof("EV-1", '{"x":1}', HASH_A)
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as conn:
                conn.execute(text("UPDATE evidence_files SET sha256 = :h"), {"h": HASH_B})
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as conn:
                conn.execute(text("UPDATE proofs SET manifest_hash = :h"), {"h": HASH_B})
        proof = self.manager.update_proof_ots("EV-1", b"ots-bytes", "PENDING")
        self.assertEqual(proof.ots_bytes, b"ots-bytes")

    def test_status_change_and_audit_are_atomic(self):
        """An invalid transition leaves neither a status change nor an event."""
        before = len(self.audit.get_events_for_evidence("EV-1"))
        with self.assertRaises(ValueError):
            self.manager.update_evidence_status("EV-1", EvidenceState.VERIFIED)
        self.assertEqual(self.manager.get_evidence("EV-1").status, EvidenceState.CREATED)
        self.assertEqual(len(self.audit.get_events_for_evidence("EV-1")), before)

if __name__ == "__main__":
    unittest.main()
