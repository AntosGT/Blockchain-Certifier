import os
import re
import shutil
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy import text
from db_helper import PostgresTestCase
from src.api.admin import ago
from src.api.app import create_app
from src.api.services import build_services
from src.core.config import Settings
from src.core.mailer import OutboxMailer
from src.core.ots import UpgradeResult
from src.core.state_machine import EvidenceState
from src.core.ots_db_worker import WORKER_LOCK_KEY

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "contrasena-de-prueba-123"
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

CHAIN = {
    "HASHED": [EvidenceState.HASHED],
    "SUBMITTED": [EvidenceState.HASHED, EvidenceState.SUBMITTED],
    "PENDING_BITCOIN": [EvidenceState.HASHED, EvidenceState.SUBMITTED, EvidenceState.PENDING_BITCOIN],
    "CONFIRMED": [EvidenceState.HASHED, EvidenceState.SUBMITTED, EvidenceState.PENDING_BITCOIN, EvidenceState.CONFIRMED],
    "VERIFIED": [EvidenceState.HASHED, EvidenceState.SUBMITTED, EvidenceState.PENDING_BITCOIN, EvidenceState.CONFIRMED,
                 EvidenceState.VERIFIED],
    "FAILED": [EvidenceState.HASHED, EvidenceState.FAILED],
}

class StagesBase(PostgresTestCase):

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.settings = Settings(_env_file=None, storage_root=os.path.join(self.tmp, "storage"),
                                 outbox_dir=os.path.join(self.tmp, "outbox"), public_base_url="http://testserver")
        self.svc = build_services(self.settings, engine=self.engine, mailer=OutboxMailer(self.settings.outbox_dir),
                                  submit_fn=submit_ok)
        self.n = 0

    def make_operation(self, request_status, evidence_status=None, title=None):
        """Creates an operation in the requested situation: request status and, if applicable, its evidence status."""
        self.n += 1
        rid = str(uuid.uuid4())
        fields = {"email": f"cliente{self.n}@example.com", "name": f"Cliente {self.n}", "country": "AR", "title": title or f"Operación {self.n}",
                  "category": "guion", "declaration_version": "1.0"}
        self.svc.requests.create(rid, fields, [], 48)
        evidence_id = None
        if evidence_status:
            evidence_id = f"PRIMA-20261004-{self.n:08d}"
            self.svc.evidence.create_evidence(evidence_id, self.user.id, fields["title"], "guion")
            self.svc.evidence.set_manifest_hash(evidence_id, HASH)
            for st in CHAIN[evidence_status]:
                self.svc.evidence.update_evidence_status(evidence_id, st)
            self.svc.evidence.save_proof(evidence_id, "{}", HASH, ots_bytes=b"OTS" if evidence_status != "HASHED" else None)
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE evidence_requests SET status = :s, evidence_id = :e WHERE id = :i"),
                         {"s": request_status, "e": evidence_id, "i": rid})
        return rid, evidence_id

    def stage_of(self, rid):
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT stage FROM operation_stage WHERE request_id = :i"), {"i": rid}).scalar()

    def runs(self, process=None):
        with self.engine.connect() as conn:
            sql = "SELECT process, lifted, advanced, failed, deferred, error FROM worker_runs"
            params = {}
            if process:
                sql += " WHERE process = :p"
                params["p"] = process
            return [dict(r._mapping) for r in conn.execute(text(sql + " ORDER BY id"), params)]


class TestStageView(StagesBase):

    def test_every_state_combination_maps_to_one_stage(self):
        cases = [
            (("PENDING_EMAIL", None), "esperando_email"),
            (("AWAITING_PAYMENT", None), "esperando_pago"),
            (("PROCESSING", None), "generando"),
            (("EXPIRED", None), "expirada"),
            (("FAILED", None), "fallida"),
            (("PROCESSED", "HASHED"), "por_sellar"),
            (("PROCESSED", "SUBMITTED"), "esperando_bitcoin"),
            (("PROCESSED", "PENDING_BITCOIN"), "esperando_bitcoin"),
            (("PROCESSED", "CONFIRMED"), "anclada"),
            (("PROCESSED", "VERIFIED"), "verificada"),
            (("PROCESSED", "FAILED"), "fallida"),
        ]
        for (req_status, ev_status), expected in cases:
            with self.subTest(f"{req_status}/{ev_status}"):
                rid, _ = self.make_operation(req_status, ev_status)
                self.assertEqual(self.stage_of(rid), expected)

    def test_each_operation_has_exactly_one_stage(self):
        for req_status, ev_status in [("PENDING_EMAIL", None), ("PROCESSED", "HASHED"), ("PROCESSED", "VERIFIED")]:
            self.make_operation(req_status, ev_status)
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT count(*), count(DISTINCT request_id) FROM operation_stage")).one()
        self.assertEqual(tuple(rows), (3, 3))

    def test_stage_since_follows_the_last_change(self):
        rid, evidence_id = self.make_operation("PROCESSED", "PENDING_BITCOIN")
        with self.engine.connect() as conn:
            last_change = conn.execute(text(
                "SELECT max(timestamp) FROM audit_events WHERE evidence_id = :e AND event_type = 'STATUS_CHANGED'"),
                {"e": evidence_id}).scalar()
            since = conn.execute(text("SELECT stage_since FROM operation_stage WHERE request_id = :i"), {"i": rid}).scalar()
        self.assertEqual(since, last_change)

        rid2, _ = self.make_operation("PENDING_EMAIL")
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT r.updated_at, v.stage_since FROM evidence_requests r "
                                    "JOIN operation_stage v ON v.request_id = r.id WHERE r.id = :i"), {"i": rid2}).one()
        self.assertEqual(row.updated_at, row.stage_since)

    def test_stage_counts_and_oldest(self):
        self.make_operation("PENDING_EMAIL")
        self.make_operation("PENDING_EMAIL")
        self.make_operation("PROCESSED", "VERIFIED")
        counts = self.svc.admin_queries.stage_counts()
        self.assertEqual(counts["esperando_email"]["n"], 2)
        self.assertEqual(counts["verificada"]["n"], 1)
        self.assertNotIn("fallida", counts)
        self.assertIsNotNone(counts["esperando_email"]["oldest"])

    def test_ago_labels(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(ago(now - timedelta(seconds=10)), "hace instantes")
        self.assertEqual(ago(now - timedelta(minutes=5)), "hace 5 min")
        self.assertEqual(ago(now - timedelta(hours=3)), "hace 3 h")
        self.assertEqual(ago(now - timedelta(days=2)), "hace 2 d")
        self.assertEqual(ago(None), "")


class TestWorkerRuns(StagesBase):

    def worker(self, submit=submit_ok, upgrade=upgrade_pending, verify=verify_ok, max_retries=3):
        from src.core.ots_db_worker import DbOTSWorker
        return DbOTSWorker(self.engine, self.svc.evidence, max_retries=max_retries,
                           submit_fn=submit, upgrade_fn=upgrade, verify_fn=verify)

    def test_each_cycle_records_one_run_per_process(self):
        self.make_operation("PROCESSED", "HASHED")
        self.worker().run_cycle()
        registrador = self.runs("registrador")
        verificador = self.runs("verificador")
        self.assertEqual(len(registrador), 1)
        self.assertEqual(len(verificador), 1)
        self.assertEqual((registrador[0]["lifted"], registrador[0]["advanced"], registrador[0]["failed"]), (1, 1, 0))
        # once stamped, the operation enters the verifier queue, which looks at it and cannot advance it yet
        self.assertEqual((verificador[0]["lifted"], verificador[0]["advanced"]), (1, 0))

    def test_empty_queues_are_recorded_too(self):
        self.worker().run_cycle()
        self.assertEqual([r["lifted"] for r in self.runs()], [0, 0])

    def test_verificador_counts_an_operation_that_advances(self):
        self.make_operation("PROCESSED", "PENDING_BITCOIN")
        self.worker(upgrade=upgrade_confirmed).run_cycle()
        run = self.runs("verificador")[0]
        self.assertEqual((run["lifted"], run["advanced"], run["failed"]), (1, 1, 0))

    def test_failures_and_waits_are_counted_separately(self):
        self.make_operation("PROCESSED", "HASHED")
        worker = self.worker(submit=submit_fail, max_retries=2)
        worker.run_cycle()
        worker.run_cycle()
        first, second = self.runs("registrador")
        self.assertEqual((first["deferred"], first["failed"]), (1, 0))   # first attempt: it waits
        self.assertEqual((second["deferred"], second["failed"]), (0, 1))  # retries exhausted: it fails

    def test_an_unexpected_error_is_recorded_and_does_not_stop_the_other_process(self):
        self.make_operation("PROCESSED", "HASHED")
        worker = self.worker()

        def boom(summary, stats):
            raise RuntimeError("falló la consulta")

        worker._upgrade_and_verify = boom
        worker.run_cycle()  # no lanza
        self.assertIsNone(self.runs("registrador")[0]["error"])
        self.assertIn("falló la consulta", self.runs("verificador")[0]["error"])
        self.assertEqual(self.runs("registrador")[0]["advanced"], 1)

    def test_no_run_is_recorded_when_another_worker_holds_the_lock(self):
        with self.engine.connect() as other:
            other.execute(text("SELECT pg_advisory_lock(:k)"), {"k": WORKER_LOCK_KEY})
            self.assertEqual(self.worker().run_cycle(), {"skipped": 1})
            other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": WORKER_LOCK_KEY})
        self.assertEqual(self.runs(), [])

    def test_old_runs_are_pruned(self):
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO worker_runs (process, started_at) VALUES ('registrador', now() - interval '20 days')"))
            conn.execute(text("INSERT INTO worker_runs (process, started_at) VALUES ('registrador', now() - interval '2 days')"))
        self.worker().run_cycle()
        with self.engine.connect() as conn:
            old = conn.execute(text("SELECT count(*) FROM worker_runs WHERE started_at < now() - interval '14 days'")).scalar()
            recent = conn.execute(text("SELECT count(*) FROM worker_runs WHERE started_at > now() - interval '3 days'")).scalar()
        self.assertEqual(old, 0)
        self.assertEqual(recent, 3)  # the one from 2 days ago and the two from this cycle


class TestStagesDashboard(StagesBase):

    def setUp(self):
        super().setUp()
        self.svc.users.create_admin(ADMIN_EMAIL, "Admin Prueba", ADMIN_PASSWORD)
        self.client = TestClient(create_app(self.settings, self.svc))
        r = self.client.post("/admin/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)

    def test_work_stays_visible_after_the_empty_runs_that_follow_it(self):
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO worker_runs (process, started_at, lifted, advanced) VALUES ('certificador', now() - interval '3 hours', 1, 1)"))
            conn.execute(text("INSERT INTO worker_runs (process, started_at, lifted, advanced, failed) VALUES ('certificador', now() - interval '2 hours', 1, 0, 1)"))
            conn.execute(text("INSERT INTO worker_runs (process, started_at) VALUES ('certificador', now() - interval '30 seconds')"))
            conn.execute(text("INSERT INTO worker_runs (process, started_at, advanced) VALUES ('certificador', now() - interval '3 days', 5)"))
        run = self.svc.admin_queries.last_runs()["certificador"]
        self.assertEqual((run["lifted"], run["advanced"], run["failed"]), (0, 0, 0))  # the last run was empty
        self.assertEqual((run["advanced_24h"], run["failed_24h"]), (1, 1))  # the one from 3 days ago does not count in the 24 h
        self.assertIsNotNone(run["last_work_at"])
        page = self.client.get("/admin").text
        self.assertIn("Último trabajo", page)
        self.assertIn("Avanzó (24 h)", page)

    def test_dashboard_shows_the_funnel_by_stage_with_filter_links(self):
        self.make_operation("PENDING_EMAIL")
        self.make_operation("PROCESSED", "HASHED")
        self.make_operation("PROCESSED", "HASHED")
        self.make_operation("PROCESSED", "VERIFIED")
        page = self.client.get("/admin").text
        for label in ("Onboarding", "Registro enviado", "Registro solicitado", "Registro aceptado"):
            self.assertIn(label, page)
        self.assertIn("/admin/operaciones?etapa=por_sellar", page)
        self.assertRegex(page, r"<b>2</b>\s*<span>Por sellar")
        self.assertRegex(page, r"<b>1</b>\s*<span>Esperando email")
        self.assertRegex(page, r"<b>0</b>\s*<span>Fallida")

    def test_processes_panel_before_and_after_a_cycle(self):
        before = self.client.get("/admin").text
        self.assertIn("Registrador", before)
        self.assertIn("Verificador", before)
        self.assertIn("Sin corridas todavía", before)

        self.make_operation("PROCESSED", "HASHED")
        self.svc.worker.run_cycle()
        after = self.client.get("/admin").text
        self.assertNotIn("Sin corridas todavía", after)
        self.assertIn("Activo", after)
        self.assertIn("hace instantes", after)

    def test_a_silent_process_is_flagged(self):
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO worker_runs (process, started_at) VALUES ('registrador', now() - interval '1 hour')"))
            conn.execute(text("INSERT INTO worker_runs (process, started_at, error) VALUES ('verificador', now(), 'RuntimeError: boom')"))
        page = self.client.get("/admin").text
        self.assertIn("Sin actividad hace más de 3 min", page)
        self.assertIn("Con error", page)

    def test_operations_list_filters_by_stage_client_and_text(self):
        self.make_operation("PENDING_EMAIL", title="Pendiente de confirmar")
        self.make_operation("PROCESSED", "HASHED", title="Guion por sellar")
        self.make_operation("PROCESSED", "VERIFIED", title="Guion verificado")
        page = self.client.get("/admin/operaciones").text
        for title in ("Pendiente de confirmar", "Guion por sellar", "Guion verificado"):
            self.assertIn(title, page)

        by_stage = self.client.get("/admin/operaciones?etapa=por_sellar").text
        self.assertIn("Guion por sellar", by_stage)
        self.assertNotIn("Guion verificado", by_stage)
        self.assertIn('<option value="por_sellar" selected>', by_stage)

        by_client = self.client.get("/admin/operaciones?cliente=cliente1@example.com").text
        self.assertIn("Pendiente de confirmar", by_client)
        self.assertNotIn("Guion por sellar", by_client)

        by_text = self.client.get("/admin/operaciones?q=verificado").text
        self.assertIn("Guion verificado", by_text)
        self.assertNotIn("Pendiente de confirmar", by_text)

        self.assertIn("No hay operaciones", self.client.get("/admin/operaciones?q=%25").text)
        self.assertEqual(self.client.get("/admin/operaciones?etapa=inventada").status_code, 200)

    def test_operations_page_requires_login_and_escapes_input(self):
        self.make_operation("PENDING_EMAIL", title="<script>alert(1)</script>")
        self.assertNotIn("<script>alert(1)</script>", self.client.get("/admin/operaciones").text)
        anonymous = TestClient(create_app(self.settings, self.svc))
        r = anonymous.get("/admin/operaciones", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/admin/login"))

    def test_request_detail_shows_its_stage(self):
        rid, _ = self.make_operation("PROCESSED", "PENDING_BITCOIN")
        page = self.client.get(f"/admin/solicitudes/{rid}").text
        self.assertRegex(page, r"Etapa</dt><dd>Esperando Bitcoin, hace")


if __name__ == "__main__":
    unittest.main()
