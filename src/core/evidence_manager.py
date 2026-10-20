from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import text
from sqlalchemy.engine import Engine
from src.models.entities import EvidenceSchema, EvidenceFileSchema, ProofSchema
from src.core.state_machine import EvidenceState, validate_transition
from src.core.audit_logger import AuditLoggerManager

_EVIDENCE_COLUMNS = ("id, user_id, title, category, description, status, privacy, manifest_hash, "
                     "previous_evidence_id, created_at, confirmed_at, declaration_version, declaration_accepted_at")

def _row_to_evidence(row) -> EvidenceSchema:
    return EvidenceSchema(
        id=row.id, user_id=str(row.user_id), title=row.title, category=row.category,
        description=row.description, status=EvidenceState(row.status), privacy=row.privacy,
        manifest_hash=row.manifest_hash, previous_evidence_id=row.previous_evidence_id,
        created_at=row.created_at, confirmed_at=row.confirmed_at,
        declaration_version=row.declaration_version, declaration_accepted_at=row.declaration_accepted_at
    )

class EvidenceManager:
    """
    Evidence lifecycle on PostgreSQL:
    - Immutability: a VERIFIED evidence is never edited (code and database trigger).
    - Versioning: a change creates a new evidence linked through previous_evidence_id.
    - Each change and its audit event go in the same transaction.
    """

    def __init__(self, engine: Engine, audit_logger: AuditLoggerManager):
        self.engine = engine
        self.audit_logger = audit_logger

    def create_evidence(
        self,
        evidence_id: str,
        user_id: str,
        title: str,
        category: str,
        description: str = "",
        previous_evidence_id: Optional[str] = None,
        actor: str = "user",
        declaration_version: Optional[str] = None
    ) -> EvidenceSchema:
        """Creates an evidence. If it versions a previous one, it must belong to the same user and be confirmed."""
        with self.engine.begin() as conn:
            if previous_evidence_id:
                parent = conn.execute(
                    text("SELECT status, user_id FROM evidences WHERE id = :i"), {"i": previous_evidence_id}
                ).first()
                if not parent:
                    raise ValueError(f"La evidencia previa {previous_evidence_id} no existe.")
                if str(parent.user_id) != str(user_id):
                    raise ValueError(f"La evidencia previa {previous_evidence_id} no pertenece al usuario.")
                if parent.status not in (EvidenceState.CONFIRMED.value, EvidenceState.VERIFIED.value):
                    raise ValueError(f"No se puede crear versión sobre una evidencia no confirmada ({previous_evidence_id}).")

            row = conn.execute(
                text("INSERT INTO evidences (id, user_id, title, category, description, status, previous_evidence_id, "
                     "declaration_version, declaration_accepted_at) "
                     "VALUES (:id, :u, :t, :c, :d, :s, :p, CAST(:dv AS varchar), "
                     "CASE WHEN CAST(:dv AS varchar) IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END) RETURNING " + _EVIDENCE_COLUMNS),
                {"id": evidence_id, "u": user_id, "t": title, "c": category, "d": description or "",
                 "s": EvidenceState.CREATED.value, "p": previous_evidence_id, "dv": declaration_version}
            ).one()
            self.audit_logger.log_event(
                evidence_id=evidence_id, event_type="EVIDENCE_CREATED", actor=actor,
                metadata={"title": title, "category": category, "previous_evidence_id": previous_evidence_id,
                          "declaration_version": declaration_version},
                conn=conn
            )
        return _row_to_evidence(row)

    def update_evidence_status(self, evidence_id: str, new_status: EvidenceState, actor: str = "system") -> EvidenceSchema:
        """Changes the status, validating the state machine and immutability, with the row locked."""
        with self.engine.begin() as conn:
            row = conn.execute(
                text("SELECT " + _EVIDENCE_COLUMNS + " FROM evidences WHERE id = :i FOR UPDATE"), {"i": evidence_id}
            ).first()
            if not row:
                raise KeyError(f"Evidencia {evidence_id} no encontrada.")
            old_status = EvidenceState(row.status)

            if old_status == EvidenceState.VERIFIED and new_status != EvidenceState.VERIFIED:
                raise ValueError(f"Regla de inmutabilidad violada: La evidencia {evidence_id} ya ha sido verificada y no puede ser modificada.")
            validate_transition(old_status, new_status)

            confirms = new_status in (EvidenceState.CONFIRMED, EvidenceState.VERIFIED)
            updated = conn.execute(
                text("UPDATE evidences SET status = :s, "
                     "confirmed_at = CASE WHEN :c AND confirmed_at IS NULL THEN CURRENT_TIMESTAMP ELSE confirmed_at END "
                     "WHERE id = :i RETURNING " + _EVIDENCE_COLUMNS),
                {"s": new_status.value, "c": confirms, "i": evidence_id}
            ).one()
            self.audit_logger.log_event(
                evidence_id=evidence_id, event_type="STATUS_CHANGED", actor=actor,
                metadata={"from": old_status.value, "to": new_status.value}, conn=conn
            )
        return _row_to_evidence(updated)

    def set_manifest_hash(self, evidence_id: str, manifest_hash: str, actor: str = "system") -> EvidenceSchema:
        """Sets the manifest hash only once; afterwards the database locks it."""
        with self.engine.begin() as conn:
            row = conn.execute(
                text("UPDATE evidences SET manifest_hash = :h WHERE id = :i RETURNING " + _EVIDENCE_COLUMNS),
                {"h": manifest_hash, "i": evidence_id}
            ).first()
            if not row:
                raise KeyError(f"Evidencia {evidence_id} no encontrada.")
            self.audit_logger.log_event(
                evidence_id=evidence_id, event_type="MANIFEST_HASHED", actor=actor,
                metadata={"manifest_hash": manifest_hash}, conn=conn
            )
        return _row_to_evidence(row)

    def get_evidence(self, evidence_id: str) -> Optional[EvidenceSchema]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT " + _EVIDENCE_COLUMNS + " FROM evidences WHERE id = :i"), {"i": evidence_id}
            ).first()
        return _row_to_evidence(row) if row else None

    def list_evidences_for_user(self, user_id: str) -> List[EvidenceSchema]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT " + _EVIDENCE_COLUMNS + " FROM evidences WHERE user_id = :u ORDER BY created_at DESC"),
                {"u": user_id}
            ).all()
        return [_row_to_evidence(r) for r in rows]

    def get_version_chain(self, evidence_id: str) -> List[EvidenceSchema]:
        """Historical chain of versions, from the first one to the given one."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("WITH RECURSIVE chain AS ("
                     " SELECT " + _EVIDENCE_COLUMNS + ", 0 AS depth FROM evidences WHERE id = :i"
                     " UNION ALL"
                     " SELECT " + ", ".join("e." + c.strip() for c in _EVIDENCE_COLUMNS.split(",")) + ", chain.depth + 1"
                     " FROM evidences e JOIN chain ON e.id = chain.previous_evidence_id)"
                     " SELECT " + _EVIDENCE_COLUMNS + " FROM chain ORDER BY depth DESC"),
                {"i": evidence_id}
            ).all()
        return [_row_to_evidence(r) for r in rows]

    # Files and proofs ---------------------------------------------------------

    def add_file(self, evidence_id: str, file_meta: Dict[str, Any], actor: str = "system") -> EvidenceFileSchema:
        with self.engine.begin() as conn:
            row = conn.execute(
                text("INSERT INTO evidence_files (evidence_id, filename, mime, size, sha256, salt_hex, storage_reference) "
                     "VALUES (:e, :f, :m, :s, :h, :salt, :ref) "
                     "RETURNING id, evidence_id, filename, mime, size, sha256, salt_hex, storage_reference, created_at"),
                {"e": evidence_id, "f": file_meta["filename"], "m": file_meta["mime"], "s": file_meta["size"],
                 "h": file_meta["sha256"], "salt": file_meta["salt_hex"], "ref": file_meta["storage_reference"]}
            ).one()
            self.audit_logger.log_event(
                evidence_id=evidence_id, event_type="FILE_ADDED", actor=actor,
                metadata={"filename": file_meta["filename"], "sha256": file_meta["sha256"]}, conn=conn
            )
        return EvidenceFileSchema(
            id=str(row.id), evidence_id=row.evidence_id, filename=row.filename, mime=row.mime, size=row.size,
            sha256=row.sha256, salt_hex=row.salt_hex, storage_reference=row.storage_reference, created_at=row.created_at
        )

    def get_files(self, evidence_id: str) -> List[EvidenceFileSchema]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, evidence_id, filename, mime, size, sha256, salt_hex, storage_reference, created_at "
                     "FROM evidence_files WHERE evidence_id = :e ORDER BY created_at, filename"),
                {"e": evidence_id}
            ).all()
        return [EvidenceFileSchema(
            id=str(r.id), evidence_id=r.evidence_id, filename=r.filename, mime=r.mime, size=r.size,
            sha256=r.sha256, salt_hex=r.salt_hex, storage_reference=r.storage_reference, created_at=r.created_at
        ) for r in rows]

    def save_proof(self, evidence_id: str, manifest_json: str, manifest_hash: str,
                   ots_bytes: Optional[bytes] = None, actor: str = "system") -> ProofSchema:
        with self.engine.begin() as conn:
            row = conn.execute(
                text("INSERT INTO proofs (evidence_id, manifest_json, manifest_hash, ots_data) "
                     "VALUES (:e, :j, :h, :o) "
                     "RETURNING id, evidence_id, manifest_json, manifest_hash, ots_data, verification_status, "
                     "bitcoin_block_height, bitcoin_tx_hash, verified_at, bitcoin_block_time"),
                {"e": evidence_id, "j": manifest_json, "h": manifest_hash, "o": ots_bytes}
            ).one()
            self.audit_logger.log_event(
                evidence_id=evidence_id, event_type="PROOF_SAVED", actor=actor,
                metadata={"manifest_hash": manifest_hash, "has_ots": ots_bytes is not None}, conn=conn
            )
        return self._row_to_proof(row)

    def update_proof_ots(self, evidence_id: str, ots_bytes: bytes, verification_status: str,
                         bitcoin_block_height: Optional[int] = None, bitcoin_tx_hash: Optional[str] = None,
                         actor: str = "system", bitcoin_block_time: Optional[datetime] = None) -> ProofSchema:
        """Updates the mutable part of the proof (.ots file and Bitcoin anchoring data)."""
        with self.engine.begin() as conn:
            row = conn.execute(
                text("UPDATE proofs SET ots_data = :o, verification_status = CAST(:v AS varchar), bitcoin_block_height = :b, "
                     "bitcoin_tx_hash = :t, bitcoin_block_time = COALESCE(:bt, bitcoin_block_time), "
                     "verified_at = CASE WHEN CAST(:v AS varchar) = 'VERIFIED' THEN CURRENT_TIMESTAMP ELSE verified_at END "
                     "WHERE evidence_id = :e "
                     "RETURNING id, evidence_id, manifest_json, manifest_hash, ots_data, verification_status, "
                     "bitcoin_block_height, bitcoin_tx_hash, verified_at, bitcoin_block_time"),
                {"o": ots_bytes, "v": verification_status, "b": bitcoin_block_height, "t": bitcoin_tx_hash, "bt": bitcoin_block_time, "e": evidence_id}
            ).first()
            if not row:
                raise KeyError(f"No hay prueba guardada para la evidencia {evidence_id}.")
            self.audit_logger.log_event(
                evidence_id=evidence_id, event_type="PROOF_UPDATED", actor=actor,
                metadata={"verification_status": verification_status, "bitcoin_block_height": bitcoin_block_height},
                conn=conn
            )
        return self._row_to_proof(row)

    def get_proof(self, evidence_id: str) -> Optional[ProofSchema]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT id, evidence_id, manifest_json, manifest_hash, ots_data, verification_status, "
                     "bitcoin_block_height, bitcoin_tx_hash, verified_at, bitcoin_block_time FROM proofs WHERE evidence_id = :e"),
                {"e": evidence_id}
            ).first()
        return self._row_to_proof(row) if row else None

    @staticmethod
    def _row_to_proof(row) -> ProofSchema:
        return ProofSchema(
            id=str(row.id), evidence_id=row.evidence_id, manifest_json=row.manifest_json,
            manifest_hash=row.manifest_hash, ots_bytes=bytes(row.ots_data) if row.ots_data is not None else None,
            verification_status=row.verification_status, bitcoin_block_height=row.bitcoin_block_height,
            bitcoin_tx_hash=row.bitcoin_tx_hash, verified_at=row.verified_at, bitcoin_block_time=row.bitcoin_block_time
        )
