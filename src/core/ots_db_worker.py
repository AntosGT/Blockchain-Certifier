import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional
from sqlalchemy import text
from sqlalchemy.engine import Engine
from opentimestamps.core.notary import VerificationError

from src.core.evidence_manager import EvidenceManager
from src.core.ots import submit_hash_to_opentimestamps, upgrade_ots, verify_bitcoin_attestation, UpgradeResult
from src.core.state_machine import EvidenceState

logger = logging.getLogger("PROOF.OTSDbWorker")

# Postgres advisory lock key: a single worker processes at a time, even with several processes.
WORKER_LOCK_KEY = 7020001

# How long the run log is kept
RUNS_RETENTION_DAYS = 14

def _new_stats() -> Dict[str, int]:
    return {"lifted": 0, "advanced": 0, "failed": 0, "deferred": 0}

class DbOTSWorker:
    """
    Idempotent worker on Postgres. It does the work of two processes of the stage plan, each with its own queue:
    - Registrar (queue: HASHED evidences without a timestamp): stamps on the calendars -> PENDING_BITCOIN.
    - Verifier (queue: SUBMITTED, PENDING_BITCOIN and CONFIRMED): upgrade; if there is a Bitcoin anchor -> CONFIRMED;
      verifies against the real block header -> VERIFIED.
    A network error leaves the evidence where it is and it is retried on the next cycle.
    A mismatch against Bitcoin (VerificationError) marks the evidence FAILED and records it in the audit log.
    Every run of every process is recorded in `worker_runs` (what it picked up, how many advanced, how many failed).
    """

    def __init__(
        self,
        engine: Engine,
        evidence_manager: EvidenceManager,
        max_retries: int = 3,
        submit_fn: Callable[[str], bytes] = submit_hash_to_opentimestamps,
        upgrade_fn: Callable[[bytes], UpgradeResult] = upgrade_ots,
        verify_fn: Callable[[bytes], Dict[str, Any]] = verify_bitcoin_attestation,
        certifier: Optional[Any] = None,
        certify_max_attempts: int = 3,
        events: Optional[Any] = None,
        delivery: Optional[Any] = None,
        control: Optional[Any] = None,
    ):
        self.engine = engine
        self.manager = evidence_manager
        self.max_retries = max_retries
        self.submit_fn = submit_fn
        self.upgrade_fn = upgrade_fn
        self.verify_fn = verify_fn
        self.certifier = certifier
        self.certify_max_attempts = certify_max_attempts
        self.events = events
        self.delivery = delivery
        self.control = control

    def run_cycle(self) -> Dict[str, int]:
        summary = {"submitted": 0, "submit_failed": 0, "failed": 0, "confirmed": 0, "verified": 0, "deferred": 0, "certified": 0}
        with self.engine.connect() as lock_conn:
            got = lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": WORKER_LOCK_KEY}).scalar()
            if not got:
                return {"skipped": 1}
            try:
                self._run_process("registrador", self._submit_pending, summary)
                self._run_process("verificador", self._upgrade_and_verify, summary)
                if self.certifier is not None:
                    self._run_process("certificador", self._certify, summary)
                if self.control is not None:
                    self._run_process("control", self.control.run, summary)
                if self.delivery is not None:
                    self._run_process("avisos", lambda summary, stats: self.delivery.deliver_pending(stats), summary)
                self._prune_runs()
            finally:
                lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": WORKER_LOCK_KEY})
                lock_conn.commit()
        return summary

    # Run log ---------------------------------------------------------

    def _run_process(self, process: str, step: Callable, summary: Dict[str, int]) -> None:
        """Runs a process, records its run and, if it fails unexpectedly, leaves the error visible and moves on to the other one."""
        started = datetime.now(timezone.utc)
        stats = _new_stats()
        error: Optional[str] = None
        try:
            step(summary, stats)
        except Exception as e:
            logger.exception("Process %s failed", process)
            error = f"{type(e).__name__}: {e}"
        with self.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO worker_runs (process, started_at, lifted, advanced, failed, deferred, error) "
                     "VALUES (:p, :s, :l, :a, :f, :d, :e)"),
                {"p": process, "s": started, "l": stats["lifted"], "a": stats["advanced"], "f": stats["failed"],
                 "d": stats["deferred"], "e": error})

    def _prune_runs(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM worker_runs WHERE started_at < now() - make_interval(days => :d)"),
                         {"d": RUNS_RETENTION_DAYS})

    # Registrador: sellado -----------------------------------------------------------

    def _submit_pending(self, summary, stats):
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT e.id, e.manifest_hash, p.id IS NOT NULL AS has_proof FROM evidences e "
                "LEFT JOIN proofs p ON p.evidence_id = e.id "
                "WHERE e.status = 'HASHED' AND (p.id IS NULL OR p.ots_data IS NULL) ORDER BY e.created_at"
            )).all()
        stats["lifted"] = len(rows)
        for row in rows:
            if not row.has_proof:
                self.manager.audit_logger.log_event(row.id, "PROOF_MISSING", "system", {})
                self.manager.update_evidence_status(row.id, EvidenceState.FAILED, actor="system")
                summary["failed"] += 1
                stats["failed"] += 1
                continue
            try:
                ots_bytes = self.submit_fn(row.manifest_hash)
            except Exception as e:
                self._register_submit_failure(row.id, str(e), summary, stats)
                continue
            self.manager.update_proof_ots(row.id, ots_bytes, "PENDING", actor="system")
            self.manager.update_evidence_status(row.id, EvidenceState.SUBMITTED, actor="system")
            self.manager.update_evidence_status(row.id, EvidenceState.PENDING_BITCOIN, actor="system")
            summary["submitted"] += 1
            stats["advanced"] += 1

    def _register_submit_failure(self, evidence_id: str, error: str, summary, stats):
        with self.engine.connect() as conn:
            previous = conn.execute(
                text("SELECT count(*) FROM audit_events WHERE evidence_id = :e AND event_type = 'OTS_SUBMISSION_FAILED' "
                     "AND seq > COALESCE((SELECT max(seq) FROM audit_events WHERE evidence_id = :e AND event_type = 'ADMIN_RETRY_SEAL'), 0)"),
                {"e": evidence_id}
            ).scalar()
        attempts = previous + 1
        self.manager.audit_logger.log_event(
            evidence_id, "OTS_SUBMISSION_FAILED", "system", {"error": error, "retry_count": attempts}
        )
        summary["submit_failed"] += 1
        if attempts >= self.max_retries:
            self.manager.update_evidence_status(evidence_id, EvidenceState.FAILED, actor="system")
            summary["failed"] += 1
            stats["failed"] += 1
        else:
            stats["deferred"] += 1

    # Verifier: upgrade and verification ---------------------------------------------

    def _upgrade_and_verify(self, summary, stats):
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT e.id, e.status, p.ots_data, p.bitcoin_block_height FROM evidences e "
                "JOIN proofs p ON p.evidence_id = e.id "
                "WHERE e.status IN ('SUBMITTED', 'PENDING_BITCOIN', 'CONFIRMED') AND p.ots_data IS NOT NULL "
                "ORDER BY e.created_at"
            )).all()
        stats["lifted"] = len(rows)
        for row in rows:
            ots_bytes, height = bytes(row.ots_data), row.bitcoin_block_height
            status = EvidenceState(row.status)
            moved = False

            if status != EvidenceState.CONFIRMED:
                try:
                    upgraded = self.upgrade_fn(ots_bytes)
                except Exception as e:
                    logger.warning("Upgrade of %s deferred: %s", row.id, e)
                    summary["deferred"] += 1
                    stats["deferred"] += 1
                    continue
                if upgraded.bitcoin_block_height is None:
                    if upgraded.changed:
                        self.manager.update_proof_ots(row.id, upgraded.ots_bytes, "PENDING", actor="system")
                    continue
                ots_bytes, height = upgraded.ots_bytes, upgraded.bitcoin_block_height
                self.manager.update_proof_ots(row.id, ots_bytes, "CONFIRMED", bitcoin_block_height=height, actor="system")
                self.manager.update_evidence_status(row.id, EvidenceState.CONFIRMED, actor="system")
                summary["confirmed"] += 1
                moved = True

            outcome = self._verify(row.id, ots_bytes, height, summary)
            if outcome == "verified":
                moved = True
            elif outcome == "failed":
                stats["failed"] += 1
            elif outcome == "deferred":
                stats["deferred"] += 1
            if moved:
                stats["advanced"] += 1

    def _verify(self, evidence_id: str, ots_bytes: bytes, height: Optional[int], summary) -> str:
        try:
            result = self.verify_fn(ots_bytes)
        except VerificationError as e:
            self.manager.audit_logger.log_event(evidence_id, "BITCOIN_VERIFICATION_FAILED", "system", {"error": str(e)})
            self.manager.update_evidence_status(evidence_id, EvidenceState.FAILED, actor="system")
            summary["failed"] += 1
            return "failed"
        except Exception as e:
            logger.warning("Verification of %s deferred: %s", evidence_id, e)
            summary["deferred"] += 1
            return "deferred"
        if not result.get("verified"):
            summary["deferred"] += 1
            return "deferred"
        block_time = result.get("block_time")
        self.manager.update_proof_ots(
            evidence_id, ots_bytes, "VERIFIED", bitcoin_block_height=result.get("height") or height, actor="system",
            bitcoin_block_time=datetime.fromtimestamp(block_time, tz=timezone.utc) if block_time else None
        )
        self.manager.update_evidence_status(evidence_id, EvidenceState.VERIFIED, actor="system")
        summary["verified"] += 1
        return "verified"

    # Certifier: PRIMA certificate -----------------------------------------------

    def _certify(self, summary, stats):
        """Queue: verified evidences without a certificate. Issues the certificate and the package of each one."""
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT e.id FROM evidences e JOIN proofs p ON p.evidence_id = e.id "
                "WHERE e.status = 'VERIFIED' AND NOT EXISTS (SELECT 1 FROM certificates c WHERE c.evidence_id = e.id) "
                "AND (SELECT count(*) FROM audit_events a WHERE a.evidence_id = e.id AND a.event_type = 'CERTIFICATE_FAILED' "
                "AND a.seq > COALESCE((SELECT max(r.seq) FROM audit_events r WHERE r.evidence_id = e.id AND r.event_type = 'CERTIFICATE_RETRY'), 0)) < :m "
                "ORDER BY e.created_at"), {"m": self.certify_max_attempts}).all()
        stats["lifted"] = len(rows)
        for row in rows:
            try:
                self.certifier.issue(row.id)
            except Exception as e:
                logger.warning("Could not issue the certificate of %s: %s", row.id, e)
                self._register_certify_failure(row.id, str(e), summary, stats)
                continue
            summary["certified"] += 1
            stats["advanced"] += 1
            if self.events is not None:
                self.events.certified(row.id)

    def _register_certify_failure(self, evidence_id: str, error: str, summary, stats):
        with self.engine.connect() as conn:
            previous = conn.execute(
                text("SELECT count(*) FROM audit_events WHERE evidence_id = :e AND event_type = 'CERTIFICATE_FAILED' "
                     "AND seq > COALESCE((SELECT max(seq) FROM audit_events WHERE evidence_id = :e AND event_type = 'CERTIFICATE_RETRY'), 0)"),
                {"e": evidence_id}).scalar()
        attempts = previous + 1
        self.manager.audit_logger.log_event(
            evidence_id, "CERTIFICATE_FAILED", "system", {"error": error, "attempt": attempts})
        if attempts >= self.certify_max_attempts:
            # The evidence stays verified and intact. The operation becomes failed so the admin can see it.
            with self.engine.begin() as conn:
                conn.execute(
                    text("UPDATE evidence_requests SET status = 'FAILED', error = :err, updated_at = CURRENT_TIMESTAMP "
                         "WHERE evidence_id = :e"),
                    {"err": f"No se pudo emitir el certificado: {error}"[:2000], "e": evidence_id})
            summary["failed"] += 1
            stats["failed"] += 1
        else:
            stats["deferred"] += 1
