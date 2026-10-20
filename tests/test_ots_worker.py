import unittest
from src.core.state_machine import EvidenceState, can_transition, validate_transition
from src.core.ots_worker import EvidenceRecord, OTSWorkerService

class TestOTSWorker(unittest.TestCase):

    def test_state_machine_valid_transitions(self):
        """Tests the valid state transitions."""
        self.assertTrue(can_transition(EvidenceState.CREATED, EvidenceState.HASHED))
        self.assertTrue(can_transition(EvidenceState.HASHED, EvidenceState.SUBMITTED))
        self.assertTrue(can_transition(EvidenceState.SUBMITTED, EvidenceState.PENDING_BITCOIN))
        self.assertTrue(can_transition(EvidenceState.PENDING_BITCOIN, EvidenceState.CONFIRMED))
        self.assertTrue(can_transition(EvidenceState.CONFIRMED, EvidenceState.VERIFIED))
        
        # Probar idempotencia (mismo estado)
        self.assertTrue(can_transition(EvidenceState.VERIFIED, EvidenceState.VERIFIED))

    def test_state_machine_invalid_transitions(self):
        """Tests that invalid transitions raise ValueError."""
        with self.assertRaises(ValueError):
            validate_transition(EvidenceState.CREATED, EvidenceState.VERIFIED)
            
        with self.assertRaises(ValueError):
            validate_transition(EvidenceState.VERIFIED, EvidenceState.CREATED)

    def test_worker_idempotency(self):
        """Tests that running the worker several times is idempotent."""
        worker = OTSWorkerService(submit_fn=lambda h: b"FAKE-OTS-" + h.encode())
        record = EvidenceRecord("EV-IDEMPOTENT-01", "a" * 64)
        
        # Primera pasada: CREATED -> SUBMITTED -> PENDING_BITCOIN
        success1 = worker.process_submission(record)
        self.assertTrue(success1)
        self.assertEqual(record.status, EvidenceState.PENDING_BITCOIN)
        self.assertIsNotNone(record.ots_bytes)
        
        # Confirm and verify
        success_upgrade = worker.process_upgrade_and_verify(
            record,
            mock_bitcoin_block=850000,
            mock_tx_hash="b" * 64
        )
        self.assertTrue(success_upgrade)
        self.assertEqual(record.status, EvidenceState.VERIFIED)
        
        # Run 10 more passes: it must not fail or change the VERIFIED status
        for _ in range(10):
            res_sub = worker.process_submission(record)
            res_upg = worker.process_upgrade_and_verify(record)
            self.assertTrue(res_sub)
            self.assertTrue(res_upg)
            self.assertEqual(record.status, EvidenceState.VERIFIED)

if __name__ == "__main__":
    unittest.main()
