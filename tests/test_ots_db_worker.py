import unittest
from sqlalchemy import text
from opentimestamps.core.notary import VerificationError
from db_helper import PostgresTestCase
from src.core.audit_logger import AuditLoggerManager
from src.core.evidence_manager import EvidenceManager
from src.core.ots import UpgradeResult
from src.core.ots_db_worker import DbOTSWorker, WORKER_LOCK_KEY
from src.core.state_machine import EvidenceState

HASH = "d" * 64

def submit_ok(h):
    return b"OTS-PENDING-" + h.encode()

def submit_fail(h):
    raise ConnectionError("calendarios caídos")

def upgrade_pending(ots):
    return UpgradeResult(ots_bytes=ots, changed=False, bitcoin_block_height=None)

def upgrade_confirmed(ots):
    return UpgradeResult(ots_bytes=b"OTS-CONFIRMED", changed=True, bitcoin_block_height=850000)

def verify_ok(ots):
    return {"verified": True, "height": 850000, "block_time": 1_700_000_000}

def verify_network_down(ots):
    raise ConnectionError("explorador sin respuesta")

def verify_mismatch(ots):
    raise VerificationError("Digest does not match merkleroot")


class TestDbWorker(PostgresTestCase):

    def setUp(self):
        super().setUp()
        self.audit = AuditLoggerManager(self.engine)
        self.manager = EvidenceManager(self.engine, self.audit)

    def _worker(self, submit=submit_ok, upgrade=upgrade_pending, verify=verify_ok, max_retries=3):
        return DbOTSWorker(self.engine, self.manager, max_retries=max_retries,
                           submit_fn=submit, upgrade_fn=upgrade, verify_fn=verify)

    def _hashed_evidence(self, evidence_id="EV-1", with_ots=False):
        self.manager.create_evidence(evidence_id, self.user.id, "Doc", "otro")
        self.manager.set_manifest_hash(evidence_id, HASH)
        self.manager.update_evidence_status(evidence_id, EvidenceState.HASHED)
        self.manager.save_proof(evidence_id, "{}", HASH, ots_bytes=b"OTS-PENDING-X" if with_ots else None)
        if with_ots:
            self.manager.update_evidence_status(evidence_id, EvidenceState.SUBMITTED)
            self.manager.update_evidence_status(evidence_id, EvidenceState.PENDING_BITCOIN)

    def _status(self, evidence_id="EV-1"):
        return self.manager.get_evidence(evidence_id).status

    def test_submits_hashed_evidence_and_waits_for_bitcoin(self):
        self._hashed_evidence()
        summary = self._worker().run_cycle()
        self.assertEqual(summary["submitted"], 1)
        self.assertEqual(self._status(), EvidenceState.PENDING_BITCOIN)
        self.assertEqual(self.manager.get_proof("EV-1").ots_bytes, b"OTS-PENDING-" + HASH.encode())

    def test_confirms_and_verifies_against_bitcoin(self):
        self._hashed_evidence(with_ots=True)
        summary = self._worker(upgrade=upgrade_confirmed).run_cycle()
        self.assertEqual((summary["confirmed"], summary["verified"]), (1, 1))
        self.assertEqual(self._status(), EvidenceState.VERIFIED)
        proof = self.manager.get_proof("EV-1")
        self.assertEqual((proof.verification_status, proof.bitcoin_block_height), ("VERIFIED", 850000))
        self.assertEqual(proof.ots_bytes, b"OTS-CONFIRMED")
        self.assertIsNotNone(proof.verified_at)

    def test_pending_stays_pending_without_bitcoin_anchor(self):
        self._hashed_evidence(with_ots=True)
        self._worker().run_cycle()
        self.assertEqual(self._status(), EvidenceState.PENDING_BITCOIN)

    def test_verification_network_error_defers_and_retries_next_cycle(self):
        self._hashed_evidence(with_ots=True)
        first = self._worker(upgrade=upgrade_confirmed, verify=verify_network_down).run_cycle()
        self.assertEqual((first["confirmed"], first["verified"], first["deferred"]), (1, 0, 1))
        self.assertEqual(self._status(), EvidenceState.CONFIRMED)
        second = self._worker(upgrade=upgrade_confirmed, verify=verify_ok).run_cycle()
        self.assertEqual(second["verified"], 1)
        self.assertEqual(self._status(), EvidenceState.VERIFIED)

    def test_bitcoin_mismatch_marks_failed_and_is_audited(self):
        self._hashed_evidence(with_ots=True)
        self._worker(upgrade=upgrade_confirmed, verify=verify_mismatch).run_cycle()
        self.assertEqual(self._status(), EvidenceState.FAILED)
        types = [e.event_type for e in self.audit.get_events_for_evidence("EV-1")]
        self.assertIn("BITCOIN_VERIFICATION_FAILED", types)

    def test_submit_failures_retry_then_fail_after_max_retries(self):
        self._hashed_evidence()
        worker = self._worker(submit=submit_fail, max_retries=3)
        worker.run_cycle()
        worker.run_cycle()
        self.assertEqual(self._status(), EvidenceState.HASHED)
        worker.run_cycle()
        self.assertEqual(self._status(), EvidenceState.FAILED)
        failed = [e for e in self.audit.get_events_for_evidence("EV-1") if e.event_type == "OTS_SUBMISSION_FAILED"]
        self.assertEqual(len(failed), 3)

    def test_failed_evidence_is_not_retried_automatically(self):
        self._hashed_evidence()
        worker = self._worker(submit=submit_fail, max_retries=1)
        worker.run_cycle()
        self.assertEqual(self._status(), EvidenceState.FAILED)
        summary = self._worker().run_cycle()
        self.assertEqual(summary["submitted"], 0)
        self.assertEqual(self._status(), EvidenceState.FAILED)

    def test_cycles_are_idempotent(self):
        self._hashed_evidence()
        worker = self._worker(upgrade=upgrade_confirmed)
        worker.run_cycle()
        worker.run_cycle()
        events_after_two = len(self.audit.get_events_for_evidence("EV-1"))
        again = worker.run_cycle()
        self.assertEqual(sum(again.values()), 0)
        self.assertEqual(len(self.audit.get_events_for_evidence("EV-1")), events_after_two)
        self.assertEqual(self._status(), EvidenceState.VERIFIED)

    def test_only_one_worker_runs_at_a_time(self):
        self._hashed_evidence()
        with self.engine.connect() as other:
            other.execute(text("SELECT pg_advisory_lock(:k)"), {"k": WORKER_LOCK_KEY})
            self.assertEqual(self._worker().run_cycle(), {"skipped": 1})
            self.assertEqual(self._status(), EvidenceState.HASHED)
            other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": WORKER_LOCK_KEY})
        self.assertEqual(self._worker().run_cycle()["submitted"], 1)

    def test_hashed_evidence_without_proof_is_failed_and_audited(self):
        self.manager.create_evidence("EV-9", self.user.id, "Doc", "otro")
        self.manager.set_manifest_hash("EV-9", HASH)
        self.manager.update_evidence_status("EV-9", EvidenceState.HASHED)
        self._worker().run_cycle()
        self.assertEqual(self._status("EV-9"), EvidenceState.FAILED)
        self.assertIn("PROOF_MISSING", [e.event_type for e in self.audit.get_events_for_evidence("EV-9")])


if __name__ == "__main__":
    unittest.main()
