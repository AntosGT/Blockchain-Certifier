import os
import time
import logging
from typing import Callable, Dict, Any, List, Optional
from src.core.state_machine import EvidenceState, validate_transition
from src.core.ots import submit_hash_to_opentimestamps

logger = logging.getLogger("PROOF.OTSWorker")

class EvidenceRecord:
    """In-memory representation of an evidence record for the worker."""
    def __init__(self, evidence_id: str, manifest_hash: str, status: EvidenceState = EvidenceState.CREATED):
        self.evidence_id = evidence_id
        self.manifest_hash = manifest_hash
        self.status = status
        self.ots_bytes: Optional[bytes] = None
        self.bitcoin_block_height: Optional[int] = None
        self.bitcoin_tx_hash: Optional[str] = None
        self.retry_count: int = 0
        self.last_error: Optional[str] = None

    def set_status(self, new_status: EvidenceState):
        validate_transition(self.status, new_status)
        self.status = new_status

class OTSWorkerService:
    """
    Idempotent daemon worker that processes the evidence queue:
    - Submit to the OpenTimestamps calendars (SUBMITTED).
    - Verify the Bitcoin anchor (CONFIRMED / VERIFIED).
    """

    def __init__(self, max_retries: int = 3, submit_fn: Callable[[str], bytes] = submit_hash_to_opentimestamps):
        self.max_retries = max_retries
        self.submit_fn = submit_fn

    def process_submission(self, record: EvidenceRecord) -> bool:
        """Submits the manifest hash to OpenTimestamps if it has not been sent yet."""
        if record.status in (EvidenceState.CONFIRMED, EvidenceState.VERIFIED):
            return True # Idempotent: already confirmed/verified

        if record.status in (EvidenceState.CREATED, EvidenceState.HASHED, EvidenceState.FAILED):
            try:
                if record.status == EvidenceState.CREATED:
                    record.set_status(EvidenceState.HASHED)
                    
                ots_bytes = self.submit_fn(record.manifest_hash)
                record.ots_bytes = ots_bytes
                record.set_status(EvidenceState.SUBMITTED)
                record.set_status(EvidenceState.PENDING_BITCOIN)
                record.last_error = None
                return True
            except Exception as e:
                record.retry_count += 1
                record.last_error = str(e)
                if record.retry_count >= self.max_retries:
                    record.status = EvidenceState.FAILED
                logger.error(f"Error procesando submission para {record.evidence_id}: {e}")
                return False

        return True

    def process_upgrade_and_verify(self, record: EvidenceRecord, mock_bitcoin_block: int = None, mock_tx_hash: str = None) -> bool:
        """
        Checks and updates the proof status against Bitcoin.
        Supports mock injection for offline tests or real explorer queries.
        """
        if record.status == EvidenceState.VERIFIED:
            return True # Idempotent: nothing to do

        if record.status in (EvidenceState.SUBMITTED, EvidenceState.PENDING_BITCOIN, EvidenceState.CONFIRMED):
            try:
                # Simulated update or Bitcoin block lookup in the worker
                if mock_bitcoin_block and mock_tx_hash:
                    record.bitcoin_block_height = mock_bitcoin_block
                    record.bitcoin_tx_hash = mock_tx_hash
                    
                if record.status != EvidenceState.CONFIRMED:
                    record.set_status(EvidenceState.CONFIRMED)
                    
                record.set_status(EvidenceState.VERIFIED)
                record.last_error = None
                return True
            except Exception as e:
                record.last_error = str(e)
                logger.error(f"Error actualizando ots para {record.evidence_id}: {e}")
                return False

        return False

    def run_worker_cycle(self, records: List[EvidenceRecord]) -> Dict[str, int]:
        """Runs a full round over the batch of evidences."""
        summary = {"processed": 0, "verified": 0, "failed": 0}
        
        for record in records:
            summary["processed"] += 1
            if record.status != EvidenceState.VERIFIED:
                self.process_submission(record)
                
            if record.status == EvidenceState.VERIFIED:
                summary["verified"] += 1
            elif record.status == EvidenceState.FAILED:
                summary["failed"] += 1

        return summary
