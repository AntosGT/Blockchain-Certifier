import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import text
from db_helper import PostgresTestCase
from src.api.app import create_app
from src.api.services import build_services
from src.core.certificate import CertificateIssuer, ChromiumPdfRenderer, DEFAULT_LAYOUT, PdfRenderer
from src.core.config import Settings
from src.core.mailer import OutboxMailer
from src.core.state_machine import EvidenceState

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "contrasena-de-prueba-123"
BLOCK_TIME = datetime(2026, 10, 4, 14, 30, tzinfo=timezone.utc)

class FakeRenderer(PdfRenderer):
    def __init__(self, fail=False):
        self.fail = fail
        self.last_html = ""
        self.calls = 0

    def render(self, html: str) -> bytes:
        self.calls += 1
        if self.fail:
            raise RuntimeError("motor de PDF caído")
        self.last_html = html
        return b"%PDF-1.4 certificado de prueba"

def fake_submit(sha256_hex):
    return b"OTS-PENDING-" + sha256_hex.encode()


class CertificateBase(PostgresTestCase):

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.renderer = FakeRenderer()
        self.build(include_originals=True)
        self.users.mark_email_verified(self.user.id)
        self.owner = self.users.get_user(self.user.id)

    def build(self, include_originals=True, renderer=None):
        self.settings = Settings(_env_file=None, storage_root=os.path.join(self.tmp, "storage"),
                                 outbox_dir=os.path.join(self.tmp, "outbox"), public_base_url="http://testserver",
                                 package_include_originals=include_originals, store_originals=include_originals,
                                 notification_delivery="external")
        self.svc = build_services(self.settings, engine=self.engine, mailer=OutboxMailer(self.settings.outbox_dir),
                                  submit_fn=fake_submit, renderer=renderer or self.renderer)

    def verified_evidence(self, title="Guion del cortometraje", files=None, block_time=BLOCK_TIME, request=False):
        files = files if files is not None else [("guion.pdf", b"%PDF-1.4 contenido del guion"), ("notas.txt", b"notas de rodaje")]
        evidence, _, _ = self.svc.pipeline.execute_creation_flow(
            user=self.owner, title=title, category="guion", description="d", files_input=files,
            declaration_accepted=True, declaration_version="1.0", defer_timestamp=True)
        ev = self.svc.evidence
        ev.update_proof_ots(evidence.id, b"OTS-PENDING", "PENDING")
        ev.update_evidence_status(evidence.id, EvidenceState.SUBMITTED)
        ev.update_evidence_status(evidence.id, EvidenceState.PENDING_BITCOIN)
        ev.update_proof_ots(evidence.id, b"OTS-ANCHORED", "CONFIRMED", bitcoin_block_height=850000)
        ev.update_evidence_status(evidence.id, EvidenceState.CONFIRMED)
        ev.update_proof_ots(evidence.id, b"OTS-ANCHORED", "VERIFIED", bitcoin_block_height=850000, bitcoin_block_time=block_time)
        ev.update_evidence_status(evidence.id, EvidenceState.VERIFIED)
        if request:
            self.link_request(evidence.id, title)
        return evidence.id

    def link_request(self, evidence_id, title):
        from uuid import uuid4
        rid = str(uuid4())
        self.svc.requests.create(rid, {"email": self.owner.email, "name": self.owner.name, "country": "AR", "title": title,
                                       "category": "guion", "declaration_version": "1.0"}, [], 48)
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE evidence_requests SET status = 'PROCESSED', evidence_id = :e WHERE id = :i"),
                         {"e": evidence_id, "i": rid})
        return rid

    def count(self, sql, **params):
        with self.engine.connect() as conn:
            return conn.execute(text(sql), params).scalar()

    def package(self, evidence_id):
        cert = self.svc.certifier.get_certificate(evidence_id)
        with open(os.path.join(self.settings.storage_root, cert["package_ref"]), "rb") as f:
            return zipfile.ZipFile(io.BytesIO(f.read()))


class TestCertificateIssue(CertificateBase):

    def test_issue_registers_certificate_files_and_audit(self):
        eid = self.verified_evidence()
        cert = self.svc.certifier.issue(eid)
        self.assertEqual(cert["template_version"], "1.4")
        self.assertEqual(cert["bitcoin_block_height"], 850000)
        self.assertEqual(cert["bitcoin_block_time"], BLOCK_TIME)
        pdf_path = os.path.join(self.settings.storage_root, cert["pdf_ref"])
        with open(pdf_path, "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest().strip(), cert["pdf_sha256"].strip())
        with open(DEFAULT_LAYOUT, encoding="utf-8") as f:
            self.assertEqual(cert["layout_sha256"].strip(), hashlib.sha256(f.read().encode("utf-8")).hexdigest())
        self.assertEqual(self.count("SELECT count(*) FROM audit_events WHERE evidence_id = :e AND event_type = 'CERTIFICATE_ISSUED'", e=eid), 1)

    def test_certificate_speaks_of_bitcoin_and_never_names_the_tool(self):
        eid = self.verified_evidence()
        self.svc.certifier.issue(eid)
        html = self.renderer.last_html
        for expected in (eid, "Guion del cortometraje", self.owner.name, "Recorded on the Bitcoin network, block 850000",
                         "2026-10-04 14:30 UTC", "Declaro que presento este contenido", "Evidence certificate"):
            self.assertIn(expected, html)
        for forbidden in ("OpenTimestamps", "opentimestamps", ".ots", "ots verify"):
            self.assertNotIn(forbidden, html)

    def test_certificate_lists_every_file_with_its_hash(self):
        eid = self.verified_evidence()
        self.svc.certifier.issue(eid)
        for f in self.svc.evidence.get_files(eid):
            self.assertIn(f.filename, self.renderer.last_html)
            self.assertIn(f.sha256, self.renderer.last_html)
        manifest_hash = self.svc.evidence.get_evidence(eid).manifest_hash
        self.assertIn(manifest_hash, self.renderer.last_html)

    def test_issue_is_idempotent(self):
        eid = self.verified_evidence()
        first = self.svc.certifier.issue(eid)
        second = self.svc.certifier.issue(eid)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.renderer.calls, 1)
        self.assertEqual(self.count("SELECT count(*) FROM audit_events WHERE evidence_id = :e AND event_type = 'CERTIFICATE_ISSUED'", e=eid), 1)

    def test_only_verified_evidence_can_be_certified(self):
        evidence, _, _ = self.svc.pipeline.execute_creation_flow(
            user=self.owner, title="Sin verificar", category="guion", description="", files_input=[("a.txt", b"x")],
            declaration_accepted=True, declaration_version="1.0", defer_timestamp=True)
        with self.assertRaises(ValueError):
            self.svc.certifier.issue(evidence.id)
        self.assertEqual(self.count("SELECT count(*) FROM certificates"), 0)
        with self.assertRaises(KeyError):
            self.svc.certifier.issue("PRIMA-NO-EXISTE")

    def test_certificates_cannot_be_edited_or_deleted(self):
        from sqlalchemy.exc import DBAPIError
        eid = self.verified_evidence()
        self.svc.certifier.issue(eid)
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as conn:
                conn.execute(text("UPDATE certificates SET pdf_sha256 = :h"), {"h": "f" * 64})
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as conn:
                conn.execute(text("DELETE FROM certificates"))

    def test_without_block_date_the_line_omits_it(self):
        eid = self.verified_evidence(block_time=None)
        self.svc.certifier.issue(eid)
        self.assertIn("Recorded on the Bitcoin network, block 850000", self.renderer.last_html)
        self.assertNotIn(", on ", self.renderer.last_html.split("Recorded on the Bitcoin network")[1][:60])

    def test_user_input_is_escaped_in_the_certificate(self):
        eid = self.verified_evidence(title="<script>alert(1)</script> & co")
        self.svc.certifier.issue(eid)
        self.assertNotIn("<script>alert(1)</script>", self.renderer.last_html)
        self.assertIn("&lt;script&gt;", self.renderer.last_html)


class TestCertificateTexts(CertificateBase):

    def add_version(self, version, **changes):
        base = json.loads(self.svc.legal.latest("certificate")["text"])
        base.update(changes)
        self.svc.legal.add_version("certificate", version, json.dumps(base, ensure_ascii=False))

    def test_wording_comes_from_the_current_text_version_and_old_certificates_keep_theirs(self):
        first = self.verified_evidence(title="Primera")
        self.svc.certifier.issue(first)
        self.add_version("2.0", document_title="Certificación de PRIMA")
        second = self.verified_evidence(title="Segunda")
        cert2 = self.svc.certifier.issue(second)
        self.assertEqual(cert2["template_version"], "2.0")
        self.assertIn("Certificación de PRIMA", self.renderer.last_html)
        self.assertEqual(self.svc.certifier.get_certificate(first)["template_version"], "1.4")

    def test_current_text_has_no_legal_notice_and_no_unmodified_clause(self):
        eid = self.verified_evidence()
        self.svc.certifier.issue(eid)
        html = self.renderer.last_html
        self.assertIn("existed at that moment", html)
        for removed in ("no fue modificado", "efectos jurídicos", "No reemplaza una opinión legal", "registros oficiales"):
            self.assertNotIn(removed, html)
        self.assertEqual(self.svc.legal.get("certificate", "1.0")["version"], "1.0")  # the previous version is kept intact

    def test_blocks_can_be_turned_off_by_configuration(self):
        self.add_version("2.0", blocks=["identificacion", "certificacion"])
        eid = self.verified_evidence()
        self.svc.certifier.issue(eid)
        html = self.renderer.last_html
        self.assertIn(eid, html)
        self.assertIn("Recorded on the Bitcoin network", html)
        self.assertNotIn("guion.pdf", html)
        self.assertNotIn("Declaro que presento este contenido", html)

    def test_unknown_placeholder_in_configured_text_does_not_break_the_certificate(self):
        self.add_version("2.0", certification_line="Bloque {block_height} y {inventado}")
        eid = self.verified_evidence()
        self.svc.certifier.issue(eid)
        self.assertIn("Bloque 850000 y {inventado}", self.renderer.last_html)

    def test_long_file_lists_are_trimmed_with_a_note(self):
        self.add_version("2.0", max_files_on_page=2)
        eid = self.verified_evidence(files=[(f"doc{i}.txt", f"contenido {i}".encode()) for i in range(5)])
        self.svc.certifier.issue(eid)
        self.assertIn("doc0.txt", self.renderer.last_html)
        self.assertNotIn("doc4.txt", self.renderer.last_html)
        self.assertIn("and 3 more files", self.renderer.last_html)

    def test_certificate_text_versions_are_immutable(self):
        from sqlalchemy.exc import DBAPIError
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as conn:
                conn.execute(text("UPDATE legal_versions SET text = '{}' WHERE kind = 'certificate'"))


class TestPackage(CertificateBase):

    def test_package_contents(self):
        eid = self.verified_evidence()
        self.svc.certifier.issue(eid)
        z = self.package(eid)
        names = set(z.namelist())
        self.assertEqual(names, {"certificate.pdf", "proof/evidence.json", "proof/evidence.sha256", "proof/proof.ots",
                                 "technical_guide.txt", "originals/guion.pdf", "originals/notas.txt"})
        self.assertEqual(z.read("originals/guion.pdf"), b"%PDF-1.4 contenido del guion")

    def test_package_proof_matches_the_certified_hash(self):
        eid = self.verified_evidence()
        self.svc.certifier.issue(eid)
        z = self.package(eid)
        manifest_hash = self.svc.evidence.get_evidence(eid).manifest_hash
        self.assertEqual(hashlib.sha256(z.read("proof/evidence.json")).hexdigest(), manifest_hash)
        self.assertEqual(z.read("proof/evidence.sha256").decode().split()[0], manifest_hash)
        self.assertEqual(z.read("proof/proof.ots"), b"OTS-ANCHORED")
        manifest = json.loads(z.read("proof/evidence.json"))
        for item in manifest["files"]:
            self.assertEqual(hashlib.sha256(z.read("originals/" + item["filename"])).hexdigest(), item["sha256"])

    def test_technical_guide_is_the_only_place_that_names_the_tool(self):
        eid = self.verified_evidence()
        self.svc.certifier.issue(eid)
        guide = self.package(eid).read("technical_guide.txt").decode("utf-8")
        self.assertIn("OpenTimestamps", guide)
        self.assertIn("ots verify -f proof/evidence.json proof/proof.ots", guide)
        self.assertIn(self.svc.evidence.get_evidence(eid).manifest_hash, guide)
        self.assertIn("block 850000", guide)
        self.assertIn("do not require access to PRIMA", guide)
        self.assertNotIn("OpenTimestamps", self.renderer.last_html)

    def test_originals_can_be_left_out(self):
        self.build(include_originals=False)
        eid = self.verified_evidence()
        self.svc.certifier.issue(eid)
        self.assertEqual(set(self.package(eid).namelist()),
                         {"certificate.pdf", "proof/evidence.json", "proof/evidence.sha256", "proof/proof.ots", "technical_guide.txt"})


class TestLegacyPackageNames(CertificateBase):

    def test_a_certificate_text_without_package_names_builds_the_package_with_the_legacy_spanish_names(self):
        base = json.loads(self.svc.legal.latest("certificate")["text"])
        self.assertEqual(base["package"]["evidence"], "proof/evidence.json")  # the current version declares its names
        del base["package"]  # like versions 1.0 to 1.2
        self.svc.legal.add_version("certificate", "2.0", json.dumps(base, ensure_ascii=False))
        eid = self.verified_evidence()
        self.svc.certifier.issue(eid)
        self.assertEqual(set(self.package(eid).namelist()),
                         {"certificado.pdf", "prueba/evidencia.json", "prueba/evidencia.sha256", "prueba/prueba.ots",
                          "technical_guide.txt", "originales/guion.pdf", "originales/notas.txt"})
        self.assertTrue(self.package(eid).read("prueba/evidencia.sha256").decode().endswith("  evidencia.json\n"))


class TestCertificadorProcess(CertificateBase):

    def runs(self):
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(text(
                "SELECT process, lifted, advanced, failed, deferred, error FROM worker_runs WHERE process = 'certificador' ORDER BY id"))]

    def stage(self, rid):
        return self.count("SELECT stage FROM operation_stage WHERE request_id = :i", i=rid)

    def test_cycle_certifies_every_verified_evidence_and_moves_the_stage(self):
        e1 = self.verified_evidence(title="Uno", request=True)
        e2 = self.verified_evidence(title="Dos", request=True)
        summary = self.svc.worker.run_cycle()
        self.assertEqual(summary["certified"], 2)
        run = self.runs()[0]
        self.assertEqual((run["lifted"], run["advanced"], run["failed"]), (2, 2, 0))
        for eid in (e1, e2):
            self.assertIsNotNone(self.svc.certifier.get_certificate(eid))
        self.assertEqual(self.count("SELECT count(*) FROM operation_stage WHERE stage = 'certificada'"), 2)
        self.assertEqual(self.count("SELECT count(*) FROM operation_stage WHERE stage = 'verificada'"), 0)

    def test_stage_is_verificada_until_the_certificate_exists(self):
        eid = self.verified_evidence(request=True)
        self.assertEqual(self.count("SELECT stage FROM operation_stage WHERE evidence_id = :e", e=eid), "verificada")
        self.svc.certifier.issue(eid)
        self.assertEqual(self.count("SELECT stage FROM operation_stage WHERE evidence_id = :e", e=eid), "certificada")

    def test_next_cycles_do_not_certify_again(self):
        self.verified_evidence()
        self.svc.worker.run_cycle()
        again = self.svc.worker.run_cycle()
        self.assertEqual(again["certified"], 0)
        self.assertEqual(self.runs()[1]["lifted"], 0)
        self.assertEqual(self.renderer.calls, 1)

    def test_failures_retry_and_then_flag_the_operation_without_touching_the_evidence(self):
        self.renderer.fail = True
        eid = self.verified_evidence(request=True)
        for _ in range(3):
            self.svc.worker.run_cycle()
        first, second, third = self.runs()
        self.assertEqual((first["deferred"], first["failed"]), (1, 0))
        self.assertEqual((second["deferred"], second["failed"]), (1, 0))
        self.assertEqual((third["deferred"], third["failed"]), (0, 1))
        self.assertEqual(self.count("SELECT count(*) FROM audit_events WHERE evidence_id = :e AND event_type = 'CERTIFICATE_FAILED'", e=eid), 3)
        self.assertEqual(self.svc.evidence.get_evidence(eid).status, EvidenceState.VERIFIED)
        self.assertEqual(self.count("SELECT stage FROM operation_stage WHERE evidence_id = :e", e=eid), "fallida")
        self.assertIn("No se pudo emitir el certificado", self.count("SELECT error FROM evidence_requests WHERE evidence_id = :e", e=eid))
        self.svc.worker.run_cycle()  # it is not retried on its own anymore
        self.assertEqual(self.renderer.calls, 3)

    def test_block_time_is_stored_by_the_verifier(self):
        from src.core.ots import UpgradeResult
        from src.core.ots_db_worker import DbOTSWorker
        evidence, _, _ = self.svc.pipeline.execute_creation_flow(
            user=self.owner, title="Verificar", category="guion", description="", files_input=[("a.txt", b"x")],
            declaration_accepted=True, declaration_version="1.0")
        worker = DbOTSWorker(
            self.engine, self.svc.evidence, submit_fn=fake_submit,
            upgrade_fn=lambda ots: UpgradeResult(ots_bytes=ots, changed=False, bitcoin_block_height=851234),
            verify_fn=lambda ots: {"verified": True, "height": 851234, "block_time": 1_791_124_200})
        worker.run_cycle()
        proof = self.svc.evidence.get_proof(evidence.id)
        self.assertEqual(proof.bitcoin_block_height, 851234)
        self.assertEqual(proof.bitcoin_block_time, datetime.fromtimestamp(1_791_124_200, tz=timezone.utc))


class TestRealChromium(CertificateBase):
    """Generates the real PDF with Chromium and reads its text."""

    def test_real_pdf_has_the_certificate_text(self):
        try:
            import pdfplumber
        except ImportError:
            self.skipTest("pdfplumber no está instalado")
        self.build(renderer=ChromiumPdfRenderer())
        eid = self.verified_evidence(title="Guion con acentos: áéíóú ñ")
        cert = self.svc.certifier.issue(eid)
        with open(os.path.join(self.settings.storage_root, cert["pdf_ref"]), "rb") as f:
            data = f.read()
        self.assertTrue(data.startswith(b"%PDF-"))
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            text_pdf = "\n".join(page.extract_text() or "" for page in pdf.pages)
            self.assertGreaterEqual(len(pdf.pages), 1)
        for expected in (eid, "Evidence certificate", "áéíóú ñ", "Recorded on the Bitcoin network, block 850000",
                         self.svc.evidence.get_evidence(eid).manifest_hash[:20]):
            self.assertIn(expected, text_pdf)
        self.assertNotIn("OpenTimestamps", text_pdf)
        self.assertNotIn("{{", text_pdf)


class TestAdminCertificate(CertificateBase):

    def setUp(self):
        super().setUp()
        self.svc.users.create_admin(ADMIN_EMAIL, "Admin Prueba", ADMIN_PASSWORD)
        self.client = TestClient(create_app(self.settings, self.svc))
        self.client.post("/admin/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, follow_redirects=False)

    def test_downloads_require_login(self):
        eid = self.verified_evidence()
        self.svc.certifier.issue(eid)
        anonymous = TestClient(create_app(self.settings, self.svc))
        for suffix in ("certificado.pdf", "paquete.zip"):
            r = anonymous.get(f"/admin/evidencias/{eid}/{suffix}", follow_redirects=False)
            self.assertEqual((r.status_code, r.headers["location"]), (303, "/admin/login"))

    def test_admin_can_download_certificate_and_package(self):
        eid = self.verified_evidence()
        self.svc.certifier.issue(eid)
        pdf = self.client.get(f"/admin/evidencias/{eid}/certificado.pdf")
        self.assertEqual((pdf.status_code, pdf.headers["content-type"]), (200, "application/pdf"))
        self.assertTrue(pdf.content.startswith(b"%PDF-"))
        package = self.client.get(f"/admin/evidencias/{eid}/paquete.zip")
        self.assertEqual((package.status_code, package.headers["content-type"]), (200, "application/zip"))
        self.assertIn("attachment", package.headers["content-disposition"])
        self.assertIn("certificate.pdf", zipfile.ZipFile(io.BytesIO(package.content)).namelist())

    def test_no_certificate_means_404(self):
        eid = self.verified_evidence()
        self.assertEqual(self.client.get(f"/admin/evidencias/{eid}/certificado.pdf").status_code, 404)
        self.assertEqual(self.client.get("/admin/evidencias/NO-EXISTE/paquete.zip").status_code, 404)

    def test_evidence_detail_shows_the_certificate_and_links(self):
        eid = self.verified_evidence()
        self.assertNotIn("Certificado de PRIMA", self.client.get(f"/admin/evidencias/{eid}").text)
        self.svc.certifier.issue(eid)
        page = self.client.get(f"/admin/evidencias/{eid}").text
        self.assertIn("Certificado de PRIMA", page)
        self.assertIn(f"/admin/evidencias/{eid}/certificado.pdf", page)
        self.assertIn("2026-10-04 14:30:00 UTC", page)  # block date

    def test_dashboard_shows_the_certificator_process_and_the_stage(self):
        self.verified_evidence(request=True)
        self.svc.worker.run_cycle()
        page = self.client.get("/admin").text
        self.assertIn("Certificador", page)
        self.assertRegex(page, r"<b>1</b>\s*<span>Certificada")
        self.assertIn("Enviado al cliente", page)


if __name__ == "__main__":
    unittest.main()
