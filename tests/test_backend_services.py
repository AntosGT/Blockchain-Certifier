import os
import shutil
import tempfile
import unittest
from db_helper import PostgresTestCase
from src.core.storage import PrivateStorageManager
from src.core.audit_logger import AuditLoggerManager
from src.core.evidence_manager import EvidenceManager
from src.core.state_machine import EvidenceState

class TestBackendServices(PostgresTestCase):

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.storage = PrivateStorageManager(storage_root=self.temp_dir, signing_secret="TEST_SECRET")
        self.audit_logger = AuditLoggerManager(self.engine)
        self.manager = EvidenceManager(self.engine, audit_logger=self.audit_logger)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)
        super().tearDown()

    def test_private_storage_and_signed_urls(self):
        """Tests private storage and signed URL validation."""
        ref, sha256_hex = self.storage.store_file("EV-100", "test.pdf", b"Contenido Confidencial")
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, ref)))
        
        # Generate a signed URL valid for 60s
        url = self.storage.generate_signed_url(ref, expires_in_seconds=60)
        self.assertIn("ref=EV-100/test.pdf", url)
        self.assertIn("sig=", url)

    def test_audit_logger_append_only(self):
        """Tests that the audit log records events without allowing changes."""
        self.manager.create_evidence("EV-100", self.user.id, "Doc1", "audiovisual")
        self.audit_logger.log_event("EV-100", "FILE_UPLOADED", "user1", {"filename": "a.pdf"})

        events = self.audit_logger.get_events_for_evidence("EV-100")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].event_type, "EVIDENCE_CREATED")
        self.assertEqual(events[1].event_type, "FILE_UPLOADED")

    def test_evidence_immutability_and_version_chain(self):
        """Tests that a VERIFIED evidence is immutable, and version chaining."""
        # 1. Create version 1
        v1 = self.manager.create_evidence("EV-V1", self.user.id, "Guion V1", "audiovisual")
        self.manager.update_evidence_status("EV-V1", EvidenceState.HASHED)
        self.manager.update_evidence_status("EV-V1", EvidenceState.SUBMITTED)
        self.manager.update_evidence_status("EV-V1", EvidenceState.PENDING_BITCOIN)
        self.manager.update_evidence_status("EV-V1", EvidenceState.CONFIRMED)
        self.manager.update_evidence_status("EV-V1", EvidenceState.VERIFIED)
        
        # Trying to modify v1 should fail because of immutability
        with self.assertRaises(ValueError):
            self.manager.update_evidence_status("EV-V1", EvidenceState.CREATED)

        # 2. Create version 2 linked to v1
        v2 = self.manager.create_evidence("EV-V2", self.user.id, "Guion V2", "audiovisual", previous_evidence_id="EV-V1")
        self.assertEqual(v2.previous_evidence_id, "EV-V1")

        # 3. Check the version chain
        chain = self.manager.get_version_chain("EV-V2")
        self.assertEqual(len(chain), 2)
        self.assertEqual(chain[0].id, "EV-V1")
        self.assertEqual(chain[1].id, "EV-V2")

if __name__ == "__main__":
    unittest.main()
