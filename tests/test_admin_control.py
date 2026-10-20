import re
import unittest
from fastapi.testclient import TestClient
from sqlalchemy import text
from db_helper import deliver_notifications
from src.api.app import create_app
from src.core import notifications as kinds
from src.core.state_machine import EvidenceState
from test_certificate import CertificateBase, ADMIN_EMAIL, ADMIN_PASSWORD

class ControlPagesBase(CertificateBase):

    def setUp(self):
        super().setUp()
        self.settings = self.settings.model_copy(update={"admin_email": "admin@example.com"})
        self.build_with_admin()

    def build_with_admin(self, **extra):
        from src.api.services import build_services
        from src.core.mailer import OutboxMailer
        self.settings = self.settings.model_copy(update=extra)
        self.mailer = OutboxMailer(self.settings.outbox_dir)
        self.svc = build_services(self.settings, engine=self.engine, mailer=self.mailer,
                                  submit_fn=lambda h: b"OTS-" + h.encode(), renderer=self.renderer)
        if not self.svc.users.get_admin_credentials(ADMIN_EMAIL):
            self.svc.users.create_admin(ADMIN_EMAIL, "Admin Prueba", ADMIN_PASSWORD)
        self.client = TestClient(create_app(self.settings, self.svc))
        self.client.post("/admin/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, follow_redirects=False)

    def csrf(self, path="/admin"):
        return re.search(r'name="csrf" value="([^"]+)"', self.client.get(path).text).group(1)

    def failed_notice(self, key="n1", kind=kinds.RECEPCION_SOLICITUD):
        nid = self.svc.notifications.enqueue(kind, key, "ana@example.com", "Ana", {"title": "Guion"})
        for _ in range(self.svc.notifications.max_attempts):
            self.svc.notifications.mark_failed(nid, "SMTP caído")
        return nid

    def status_of(self, nid):
        return self.svc.notifications.get(nid)["status"]


class TestNoticesPage(ControlPagesBase):

    def test_requires_login_everywhere(self):
        anonymous = TestClient(create_app(self.settings, self.svc))
        nid = self.failed_notice()
        for method, path in (("get", "/admin/avisos"), ("post", f"/admin/avisos/{nid}/reenviar")):
            r = getattr(anonymous, method)(path, follow_redirects=False)
            self.assertEqual((r.status_code, r.headers["location"]), (303, "/admin/login"))
        self.assertEqual(self.status_of(nid), "failed")

    def test_lists_counts_and_filters(self):
        failed = self.failed_notice("a")
        sent = self.svc.notifications.enqueue(kinds.RECEPCION_PAGO, "b", "beto@example.com", "Beto", {"title": "Otro"})
        self.svc.notifications.mark_sent(sent)
        self.svc.notifications.enqueue(kinds.CONFIRMACION_REGISTRO, "c", "caro@example.com", "Caro", {"title": "Tercero"})
        page = self.client.get("/admin/avisos").text
        for email in ("ana@example.com", "beto@example.com", "caro@example.com"):
            self.assertIn(email, page)
        self.assertRegex(page, r"<b>1</b><span>Pendientes")
        self.assertRegex(page, r"<b>1</b><span>Fallidos")
        only_failed = self.client.get("/admin/avisos?estado=failed").text
        self.assertIn("ana@example.com", only_failed)
        self.assertNotIn("beto@example.com", only_failed)
        self.assertIn("SMTP caído", only_failed)
        by_kind = self.client.get("/admin/avisos?tipo=cliente.recepcion_pago").text
        self.assertIn("beto@example.com", by_kind)
        self.assertNotIn("ana@example.com", by_kind)
        self.assertEqual(self.client.get("/admin/avisos?estado=inventado&tipo=inventado").status_code, 200)

    def test_resend_button_only_for_failed_notices(self):
        failed = self.failed_notice("a")
        pending = self.svc.notifications.enqueue(kinds.RECEPCION_PAGO, "b", "beto@example.com", "Beto", {"title": "Otro"})
        page = self.client.get("/admin/avisos").text
        self.assertIn(f"/admin/avisos/{failed}/reenviar", page)
        self.assertNotIn(f"/admin/avisos/{pending}/reenviar", page)

    def test_requeue_a_failed_notice(self):
        nid = self.failed_notice()
        r = self.client.post(f"/admin/avisos/{nid}/reenviar", data={"csrf": self.csrf("/admin/avisos")}, follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/admin/avisos"))
        self.assertEqual(self.status_of(nid), "pending")
        self.assertEqual(self.svc.notifications.get(nid)["attempts"], 0)

    def test_requeue_needs_a_valid_csrf_token_and_a_failed_notice(self):
        nid = self.failed_notice()
        for data in ({}, {"csrf": "falso"}):
            self.assertEqual(self.client.post(f"/admin/avisos/{nid}/reenviar", data=data).status_code, 403)
        self.assertEqual(self.status_of(nid), "failed")
        pending = self.svc.notifications.enqueue(kinds.RECEPCION_PAGO, "p", "x@example.com", "X", {})
        r = self.client.post(f"/admin/avisos/{pending}/reenviar", data={"csrf": self.csrf()})
        self.assertEqual(r.status_code, 400)

    def test_user_input_is_escaped(self):
        self.svc.notifications.enqueue(kinds.RECEPCION_SOLICITUD, "x", "a@example.com", "A", {"title": "<script>alert(1)</script>"},
                                       request_id=self.link_request(self.verified_evidence(), "t"))
        self.assertNotIn("<script>alert(1)</script>", self.client.get("/admin/avisos").text)


class TestDashboardSignals(ControlPagesBase):

    def test_no_banners_when_everything_is_fine(self):
        page = self.client.get("/admin").text
        self.assertNotIn("fallido", page.split("Operaciones por etapa")[0])
        self.assertNotIn("pendiente hace más de 10 minutos", page)

    def test_failed_notices_raise_a_banner_with_a_link(self):
        self.failed_notice("a")
        self.failed_notice("b", kind=kinds.RECEPCION_PAGO)
        page = self.client.get("/admin").text
        self.assertIn("2 avisos fallidos", page)
        self.assertIn("/admin/avisos?estado=failed", page)

    def test_old_pending_notices_raise_a_banner(self):
        self.svc.notifications.enqueue(kinds.RECEPCION_SOLICITUD, "k", "a@example.com", "A", {})
        self.assertNotIn("hace más de 10 minutos", self.client.get("/admin").text)
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE notifications SET created_at = now() - interval '30 minutes'"))
        self.assertIn("1 aviso pendiente hace más de 10 minutos", self.client.get("/admin").text)

    def test_overdue_operations_are_flagged_in_the_funnel_and_the_list(self):
        rid = self.link_request(self.verified_evidence(), "Guion atrasado")
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE evidence_requests SET status = 'PROCESSING', evidence_id = NULL, "
                              "updated_at = now() - interval '20 minutes' WHERE id = :i"), {"i": rid})
        dashboard = self.client.get("/admin").text
        self.assertRegex(dashboard, r"1 atrasada")
        listing = self.client.get("/admin/operaciones").text
        self.assertIn("atrasada</span>", listing)
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE evidence_requests SET updated_at = now() WHERE id = :i"), {"i": rid})
        self.assertNotIn("atrasada</span>", self.client.get("/admin/operaciones").text)

    def test_panel_lists_control_and_certifier_and_the_local_consumer(self):
        self.svc.worker.run_cycle()
        page = self.client.get("/admin").text
        for process in ("Registrador", "Verificador", "Certificador", "Control", "Avisos"):
            self.assertIn(process, page)

    def test_panel_hides_the_local_consumer_when_n8n_delivers(self):
        self.build_with_admin(notification_delivery="external")
        page = self.client.get("/admin").text
        self.assertIn("Control", page)
        self.assertNotIn("Envía los correos al cliente y a la administración", page)


class TestRequestActions(ControlPagesBase):

    def make_request(self, status):
        from uuid import uuid4
        rid = str(uuid4())
        self.svc.requests.create(rid, {"email": "ana@example.com", "name": "Ana Prueba", "country": "AR", "title": "Guion",
                                       "category": "guion", "declaration_version": "1.0"}, [], 48)
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE evidence_requests SET status = :s WHERE id = :i"), {"s": status, "i": rid})
        return rid

    def test_the_email_confirmation_notice_has_its_label_and_filter_in_the_notices_page(self):
        # This notification is born before the request exists: it has no associated operation
        self.svc.notifications.enqueue(kinds.CONFIRMACION_EMAIL, "v1", "ana@example.com", None,
                                       {"form_url": "http://x/formulario?t=abc", "expires_hours": 24})
        page = self.client.get("/admin/avisos").text
        self.assertIn("<td>Confirmación de email</td>", page)  # the cell shows the label, not the type code
        filtered = self.client.get(f"/admin/avisos?tipo={kinds.CONFIRMACION_EMAIL}").text
        self.assertIn("ana@example.com", filtered)
        self.assertNotIn("formulario?t=abc", page)  # the link with the token is not shown on the dashboard

    def test_request_detail_lists_its_notices(self):
        rid = self.make_request("PENDING_EMAIL")
        self.svc.notifications.enqueue(kinds.RECEPCION_SOLICITUD, rid, "ana@example.com", "Ana", {"title": "Guion"}, request_id=rid)
        page = self.client.get(f"/admin/solicitudes/{rid}").text
        self.assertIn("Avisos", page)
        self.assertIn("Recepción de solicitud", page)
        self.assertIn("Pendiente", page)

    def test_the_admin_has_no_way_to_resend_the_old_verification_link(self):
        # The form now opens with a link requested by the client: there is no per-request verification link
        for status in ("PENDING_EMAIL", "EXPIRED", "PROCESSED"):
            rid = self.make_request(status)
            self.assertNotIn("reenviar-enlace", self.client.get(f"/admin/solicitudes/{rid}").text)
            r = self.client.post(f"/admin/solicitudes/{rid}/reenviar-enlace", data={"csrf": self.csrf()})
            self.assertIn(r.status_code, (404, 405))
            self.assertEqual(self.svc.notifications.list(request_id=rid), [])


class TestEvidenceActions(ControlPagesBase):

    def failed_seal_evidence(self):
        evidence, _, _ = self.svc.pipeline.execute_creation_flow(
            user=self.owner, title="Sellado fallido", category="guion", description="", files_input=[("a.txt", b"contenido")],
            declaration_accepted=True, declaration_version="1.0", defer_timestamp=True)
        self.svc.evidence.update_evidence_status(evidence.id, EvidenceState.FAILED)
        return evidence.id

    def test_retry_seal_button_and_action(self):
        eid = self.failed_seal_evidence()
        page = self.client.get(f"/admin/evidencias/{eid}").text
        self.assertIn("Reintentar el sellado", page)
        before = self.svc.evidence.get_evidence(eid)
        r = self.client.post(f"/admin/evidencias/{eid}/reintentar-sellado", data={"csrf": self.csrf(f"/admin/evidencias/{eid}")},
                             follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (303, f"/admin/evidencias/{eid}"))
        after = self.svc.evidence.get_evidence(eid)
        self.assertEqual(after.status, EvidenceState.HASHED)
        self.assertEqual(after.manifest_hash, before.manifest_hash)  # the hash is not touched
        events = [(e.event_type, e.actor) for e in self.svc.audit.get_events_for_evidence(eid)]
        self.assertIn(("ADMIN_RETRY_SEAL", ADMIN_EMAIL), events)
        self.assertNotIn("Reintentar el sellado", self.client.get(f"/admin/evidencias/{eid}").text)

    def test_the_worker_picks_the_retried_evidence_up_again(self):
        eid = self.failed_seal_evidence()
        self.client.post(f"/admin/evidencias/{eid}/reintentar-sellado", data={"csrf": self.csrf()})
        self.svc.worker.run_cycle()
        self.assertEqual(self.svc.evidence.get_evidence(eid).status, EvidenceState.PENDING_BITCOIN)

    def test_failures_after_a_retry_start_counting_from_zero(self):
        from src.core.ots_db_worker import DbOTSWorker
        eid = self.failed_seal_evidence()
        for _ in range(3):
            self.svc.audit.log_event(eid, "OTS_SUBMISSION_FAILED", "system", {})
        self.client.post(f"/admin/evidencias/{eid}/reintentar-sellado", data={"csrf": self.csrf()})

        def broken(h):
            raise ConnectionError("calendarios caídos")

        worker = DbOTSWorker(self.engine, self.svc.evidence, max_retries=3, submit_fn=broken)
        worker.run_cycle()
        self.assertEqual(self.svc.evidence.get_evidence(eid).status, EvidenceState.HASHED)  # first attempt after the retry: it does not fail right away

    def test_retry_seal_is_refused_when_it_does_not_apply(self):
        verified = self.verified_evidence()
        csrf = self.csrf()
        self.assertEqual(self.client.post(f"/admin/evidencias/{verified}/reintentar-sellado", data={"csrf": csrf}).status_code, 400)
        self.assertEqual(self.client.post("/admin/evidencias/NO-EXISTE/reintentar-sellado", data={"csrf": csrf}).status_code, 404)
        eid = self.failed_seal_evidence()
        self.assertEqual(self.client.post(f"/admin/evidencias/{eid}/reintentar-sellado", data={"csrf": "falso"}).status_code, 403)
        self.assertEqual(self.svc.evidence.get_evidence(eid).status, EvidenceState.FAILED)

    def test_a_failure_that_is_not_about_sealing_cannot_be_retried_from_here(self):
        eid = self.failed_seal_evidence()
        self.svc.evidence.update_proof_ots(eid, b"OTS", "PENDING")  # it already had a timestamp: the failure was something else (for example, against the block)
        r = self.client.post(f"/admin/evidencias/{eid}/reintentar-sellado", data={"csrf": self.csrf()})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.svc.evidence.get_evidence(eid).status, EvidenceState.FAILED)

    def test_retry_certificate_after_the_certifier_gave_up(self):
        eid = self.verified_evidence(request=True)
        self.renderer.fail = True
        for _ in range(3):
            self.svc.worker.run_cycle()
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT stage FROM operation_stage WHERE evidence_id = :e"), {"e": eid}).scalar(), "fallida")
        self.assertIn("Reintentar el certificado", self.client.get(f"/admin/evidencias/{eid}").text)
        self.renderer.fail = False
        r = self.client.post(f"/admin/evidencias/{eid}/reintentar-certificado", data={"csrf": self.csrf()}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn(("CERTIFICATE_RETRY", ADMIN_EMAIL), [(e.event_type, e.actor) for e in self.svc.audit.get_events_for_evidence(eid)])
        self.svc.worker.run_cycle()
        self.assertIsNotNone(self.svc.certifier.get_certificate(eid))
        with self.engine.connect() as conn:
            self.assertIn(conn.execute(text("SELECT stage FROM operation_stage WHERE evidence_id = :e"), {"e": eid}).scalar(),
                          ("certificada", "entregada"))

    def test_retry_certificate_is_refused_when_it_does_not_apply(self):
        eid = self.verified_evidence()
        self.svc.certifier.issue(eid)
        csrf = self.csrf()
        self.assertEqual(self.client.post(f"/admin/evidencias/{eid}/reintentar-certificado", data={"csrf": csrf}).status_code, 400)
        unverified = TestEvidenceActions.failed_seal_evidence(self)
        self.assertEqual(self.client.post(f"/admin/evidencias/{unverified}/reintentar-certificado", data={"csrf": csrf}).status_code, 400)
        self.assertEqual(self.client.post(f"/admin/evidencias/{eid}/reintentar-certificado", data={"csrf": "falso"}).status_code, 403)

    def test_actions_need_a_session(self):
        eid = self.failed_seal_evidence()
        anonymous = TestClient(create_app(self.settings, self.svc))
        for suffix in ("reintentar-sellado", "reintentar-certificado"):
            r = anonymous.post(f"/admin/evidencias/{eid}/{suffix}", data={"csrf": "x"}, follow_redirects=False)
            self.assertEqual((r.status_code, r.headers["location"]), (303, "/admin/login"))
        self.assertEqual(self.svc.evidence.get_evidence(eid).status, EvidenceState.FAILED)


if __name__ == "__main__":
    unittest.main()
