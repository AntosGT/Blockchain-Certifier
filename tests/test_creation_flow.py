import json
import shutil
import tempfile
import unittest
from sqlalchemy import text
from db_helper import PostgresTestCase
from src.core.storage import PrivateStorageManager
from src.core.audit_logger import AuditLoggerManager
from src.core.evidence_manager import EvidenceManager
from src.core.legal import LegalVersionManager
from src.core.ots_worker import OTSWorkerService
from src.core.payments import BypassPaymentGate, PaymentGate, PaymentResult
from src.core.creation_flow import EvidenceCreationPipeline, EvidenceValidationError
from src.core.state_machine import EvidenceState
from src.models.entities import UserSchema

FILES = [
    ("propuesta_comercial.pdf", b"%PDF-1.4 Fake PDF Content for Test"),
    ("esquema_tecnico.png", b"\x89PNGFakeImageBytes"),
]
TEXT_ENTRY = {
    "title": "Nota de Idea Adicional",
    "content": "Detalles adicionales del proyecto expresados en texto.",
    "authors": "Test Author",
}

class DenyGate(PaymentGate):
    def verify(self, user, amount_usd, payment_reference=None):
        return PaymentResult(confirmed=False, method="deny", reference=None, amount_usd=amount_usd)

def fake_submit(sha256_hex):
    return b"FAKE-OTS-" + sha256_hex.encode()

def failing_submit(sha256_hex):
    raise ConnectionError("calendarios no disponibles")


class TestCreationFlow(PostgresTestCase):

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.users.mark_email_verified(self.user.id)
        self.user = self.users.get_user(self.user.id)
        self.storage = PrivateStorageManager(storage_root=self.temp_dir)
        self.audit_logger = AuditLoggerManager(self.engine)
        self.evidence_manager = EvidenceManager(self.engine, audit_logger=self.audit_logger)
        self.legal = LegalVersionManager(self.engine)
        self.pipeline = self._pipeline(fake_submit)

    def _pipeline(self, submit_fn, gate=None):
        return EvidenceCreationPipeline(
            storage_manager=self.storage, evidence_manager=self.evidence_manager,
            ots_worker=OTSWorkerService(submit_fn=submit_fn), legal_versions=self.legal,
            payment_gate=gate or BypassPaymentGate(), price_usd=0.0
        )

    def _run(self, pipeline=None, **overrides):
        args = dict(user=self.user, title="Proyecto Fragola Alpha", category="propuesta_comercial",
                    description="Propuesta de arquitectura y diseño", files_input=FILES, text_input=TEXT_ENTRY,
                    declaration_accepted=True, declaration_version="1.0")
        args.update(overrides)
        return (pipeline or self.pipeline).execute_creation_flow(**args)

    def _count(self, table):
        with self.engine.connect() as conn:
            return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()

    def test_full_7_step_creation_flow(self):
        summary = self.pipeline.build_summary(
            title="Proyecto Fragola Alpha", category="propuesta_comercial", description="Propuesta",
            user=self.user, files_input=FILES, text_input=TEXT_ENTRY, declaration_version="1.0"
        )
        self.assertEqual(summary["files_count"], 3)
        self.assertEqual(summary["declaration_version"], "1.0")
        self.assertEqual(summary["owner"]["email"], self.user.email)
        self.assertEqual(summary["price_usd"], 0.0)
        self.assertIn("date", summary)

        evidence, manifest_dict, manifest_hash = self._run()

        self.assertRegex(evidence.id, r"^PRIMA-\d{8}-[0-9A-F]{8}$")
        self.assertEqual(evidence.manifest_hash, manifest_hash)
        self.assertEqual(evidence.status, EvidenceState.PENDING_BITCOIN)
        self.assertEqual(len(manifest_dict["files"]), 3)

        files = self.evidence_manager.get_files(evidence.id)
        self.assertEqual(len(files), 3)
        self.assertEqual({f.mime for f in files}, {"application/pdf", "image/png", "text/markdown"})
        proof = self.evidence_manager.get_proof(evidence.id)
        self.assertEqual(proof.manifest_hash, manifest_hash)
        self.assertTrue(proof.ots_bytes.startswith(b"FAKE-OTS-"))

        events = [e.event_type for e in self.audit_logger.get_events_for_evidence(evidence.id)]
        self.assertEqual(events[0], "EVIDENCE_CREATED")
        self.assertEqual(events[-1], "STATUS_CHANGED")

    def test_declaration_version_is_recorded_on_evidence_and_manifest(self):
        evidence, manifest_dict, _ = self._run()
        self.assertEqual(evidence.declaration_version, "1.0")
        self.assertIsNotNone(evidence.declaration_accepted_at)
        self.assertEqual(manifest_dict["declaration"]["version"], "1.0")
        stored = json.loads(self.evidence_manager.get_proof(evidence.id).manifest_json)
        self.assertEqual(stored["declaration"], {"text": self.legal.get("declaration", "1.0")["text"], "version": "1.0"})

    def test_exact_version_accepted_is_kept_when_a_newer_one_exists(self):
        self.legal.add_version("declaration", "2.0", "Texto nuevo de la declaración.")
        evidence, manifest_dict, _ = self._run(declaration_version="2.0")
        self.assertEqual(evidence.declaration_version, "2.0")
        self.assertEqual(manifest_dict["declaration"]["text"], "Texto nuevo de la declaración.")

    def test_declaration_is_required_and_creates_nothing(self):
        with self.assertRaises(PermissionError):
            self._run(declaration_accepted=False)
        with self.assertRaises(EvidenceValidationError):
            self._run(declaration_version="9.9")
        self.assertEqual(self._count("evidences"), 0)
        self.assertEqual(self._count("audit_events"), 0)

    def test_payment_gate_denial_creates_nothing(self):
        with self.assertRaises(PermissionError):
            self._run(pipeline=self._pipeline(fake_submit, DenyGate()))
        self.assertEqual(self._count("evidences"), 0)

    def test_bypass_payment_is_recorded_in_audit_log(self):
        evidence, _, _ = self._run(payment_reference="prueba-interna")
        paid = [e for e in self.audit_logger.get_events_for_evidence(evidence.id) if e.event_type == "PAYMENT_CONFIRMED"]
        self.assertEqual(len(paid), 1)
        self.assertEqual(paid[0].metadata["method"], "bypass")
        self.assertEqual(paid[0].metadata["reference"], "prueba-interna")

    def test_unverified_email_is_rejected(self):
        other = self.users.create_user(UserSchema(email="otro@example.com", name="Otra Persona", country="AR"))
        with self.assertRaises(PermissionError):
            self._run(user=other)
        self.assertEqual(self._count("evidences"), 0)

    def test_invalid_inputs_are_rejected_before_creating_anything(self):
        bad_cases = [
            dict(title="   "),
            dict(category="inventada"),
            dict(files_input=[], text_input=None),
            dict(files_input=[("../escape.pdf", b"x")]),
            dict(files_input=[("a/b.pdf", b"x")]),
            dict(files_input=[("virus.exe", b"x")]),
            dict(files_input=[("vacio.pdf", b"")]),
            dict(files_input=[("a.pdf", b"x"), ("A.PDF", b"y")]),
            dict(files_input=[], text_input={"title": "t", "content": "   "}),
            dict(user=self.user.model_copy(update={"country": "ar"})),
            dict(user=self.user.model_copy(update={"country": "ZZ"})),
            dict(user=self.user.model_copy(update={"name": " "})),
        ]
        for overrides in bad_cases:
            with self.subTest(overrides=list(overrides)):
                with self.assertRaises(EvidenceValidationError):
                    self._run(**overrides)
        self.assertEqual(self._count("evidences"), 0)

    def test_timestamp_failure_after_payment_leaves_evidence_hashed_for_retry(self):
        evidence, _, manifest_hash = self._run(pipeline=self._pipeline(failing_submit))
        self.assertEqual(evidence.status, EvidenceState.HASHED)
        proof = self.evidence_manager.get_proof(evidence.id)
        self.assertEqual(proof.manifest_hash, manifest_hash)
        self.assertIsNone(proof.ots_bytes)
        failed = [e for e in self.audit_logger.get_events_for_evidence(evidence.id) if e.event_type == "OTS_SUBMISSION_FAILED"]
        self.assertEqual(len(failed), 1)
        self.assertIn("calendarios no disponibles", failed[0].metadata["error"])

    def test_cannot_version_someone_elses_evidence(self):
        evidence, _, _ = self._run()
        for st in (EvidenceState.CONFIRMED, EvidenceState.VERIFIED):
            self.evidence_manager.update_evidence_status(evidence.id, st)
        other = self.users.create_user(UserSchema(email="otro@example.com", name="Otra Persona", country="AR"))
        with self.assertRaises(ValueError):
            self.evidence_manager.create_evidence("EV-X", other.id, "V2", "idea", previous_evidence_id=evidence.id)


if __name__ == "__main__":
    unittest.main()
