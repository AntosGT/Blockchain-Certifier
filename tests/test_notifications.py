import io
import os
import re
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy import text
from db_helper import PostgresTestCase, deliver_notifications, link_token
from src.api.app import create_app
from src.api.services import build_services
from src.core import notifications as kinds
from src.core.config import Settings
from src.core.mailer import OutboxMailer
from src.core.notification_templates import render_email
from src.core.notifications import NotificationManager, BACKOFF_MINUTES
from test_certificate import FakeRenderer

FORM = {
    "title": "Guion del cortometraje", "category": "guion", "description": "Primera versión",
    "name": "Ana Prueba", "email": "ana@example.com", "country": "AR", "document_id": "",
    "declaration_version": "1.0", "declaration_accepted": "1",
}

def fake_submit(sha256_hex):
    return b"FAKE-OTS-" + sha256_hex.encode()


class TestNotificationQueue(PostgresTestCase):

    def setUp(self):
        super().setUp()
        self.n = NotificationManager(self.engine, max_attempts=3)

    def enqueue(self, key="k1", kind=kinds.RECEPCION_SOLICITUD, **kw):
        return self.n.enqueue(kind, key, "ana@example.com", "Ana", {"title": "T"}, **kw)

    def test_same_notice_is_not_queued_twice(self):
        first = self.enqueue()
        self.assertIsNotNone(first)
        self.assertIsNone(self.enqueue())
        self.assertIsNotNone(self.enqueue(key="k2"))
        self.assertIsNotNone(self.enqueue(kind=kinds.RECEPCION_PAGO))  # another type with the same key
        self.assertEqual(self.n.counts(), {"pending": 3})

    def test_wake_is_called_only_when_a_new_notice_is_queued(self):
        calls = []
        self.n.wake = lambda: calls.append(1)
        self.enqueue("a")
        self.assertEqual(len(calls), 1)
        self.enqueue("a")  # repeated: not queued, does not wake
        self.assertEqual(len(calls), 1)
        self.enqueue("b")
        self.assertEqual(len(calls), 2)

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            self.n.enqueue("cliente.inventado", "k", "a@b.com", "A", {})

    def test_leased_notices_are_not_given_to_a_second_consumer(self):
        self.enqueue("a")
        self.enqueue("b")
        first = self.n.lease_pending(10)
        self.assertEqual(len(first), 2)
        self.assertEqual(self.n.lease_pending(10), [])  # still pending but reserved
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE notifications SET next_attempt_at = now() - interval '1 minute'"))
        self.assertEqual(len(self.n.lease_pending(10)), 2)  # once the unconfirmed lease expires, they reappear

    def test_lease_respects_the_limit_and_the_order(self):
        for i in range(5):
            self.enqueue(f"k{i}")
        leased = self.n.lease_pending(2)
        self.assertEqual([x["dedupe_key"] for x in leased], ["k0", "k1"])

    def test_marking_sent_is_idempotent(self):
        nid = self.enqueue()
        self.assertTrue(self.n.mark_sent(nid))
        sent_at = self.n.get(nid)["sent_at"]
        self.assertTrue(self.n.mark_sent(nid))
        self.assertEqual(self.n.get(nid)["sent_at"], sent_at)
        self.assertEqual(self.n.get(nid)["status"], "sent")
        self.assertEqual(self.n.lease_pending(10), [])

    def test_failures_back_off_and_then_fail(self):
        nid = self.enqueue()
        self.assertEqual(self.n.mark_failed(nid, "SMTP caído"), "pending")
        notice = self.n.get(nid)
        self.assertEqual((notice["attempts"], notice["last_error"]), (1, "SMTP caído"))
        wait = (notice["next_attempt_at"] - datetime.now(timezone.utc)).total_seconds() / 60
        self.assertTrue(BACKOFF_MINUTES[0] - 0.5 < wait <= BACKOFF_MINUTES[0] + 0.1)
        self.assertEqual(self.n.mark_failed(nid, "SMTP caído"), "pending")
        self.assertEqual(self.n.mark_failed(nid, "SMTP caído"), "failed")
        self.assertEqual(self.n.get(nid)["status"], "failed")
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE notifications SET next_attempt_at = now() - interval '1 hour'"))
        self.assertEqual(self.n.lease_pending(10), [])  # a failed notification does not come back on its own

    def test_a_sent_notice_cannot_be_marked_failed(self):
        nid = self.enqueue()
        self.n.mark_sent(nid)
        self.assertIsNone(self.n.mark_failed(nid, "tarde"))
        self.assertEqual(self.n.get(nid)["status"], "sent")

    def test_manual_requeue_only_for_failed_notices(self):
        nid = self.enqueue()
        self.assertFalse(self.n.requeue(nid))  # pending: nothing to requeue
        for _ in range(3):
            self.n.mark_failed(nid, "x")
        self.assertTrue(self.n.requeue(nid))
        notice = self.n.get(nid)
        self.assertEqual((notice["status"], notice["attempts"], notice["last_error"]), ("pending", 0, None))
        self.assertEqual(len(self.n.lease_pending(10)), 1)

    def test_list_filters(self):
        a = self.enqueue("a")
        self.enqueue("b", kind=kinds.RECEPCION_PAGO)
        self.n.mark_sent(a)
        self.assertEqual(len(self.n.list(status="sent")), 1)
        self.assertEqual(len(self.n.list(kind=kinds.RECEPCION_PAGO)), 1)
        self.assertEqual(self.n.get("no-es-un-uuid"), None)


class NoticeFlowBase(PostgresTestCase):

    def make(self, **overrides):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        cfg = dict(_env_file=None, storage_root=os.path.join(self.tmp, "storage"), outbox_dir=os.path.join(self.tmp, "outbox"),
                   public_base_url="http://testserver", admin_email="admin@example.com")
        cfg.update(overrides)
        self.settings = Settings(**cfg)
        self.mailer = OutboxMailer(self.settings.outbox_dir)
        self.renderer = FakeRenderer()
        self.svc = build_services(self.settings, engine=self.engine, mailer=self.mailer, submit_fn=fake_submit, renderer=self.renderer)
        self.client = TestClient(create_app(self.settings, self.svc))
        return self.client

    def submit(self, **data):
        """Submits the form with the emailed link: the evidence is generated right away."""
        form = dict(FORM, **data)
        if "t" not in form:  # a new link voids the previous ones for the same email: only created if the test does not bring its own
            form["t"] = link_token(self.svc, form["email"])
        r = self.client.post("/solicitudes", data=form,
                             files=[("files", ("guion.pdf", b"%PDF-1.4 contenido", "application/pdf"))], follow_redirects=False)
        self.assertEqual(r.status_code, 303, r.text)
        return r.headers["location"]

    def notices(self, **filters):
        return self.svc.notifications.list(**filters)

    def client_notice(self, email="ana@example.com", lang="es"):
        """Requests the registration: queues the link notification, the only email to the client before the certificate."""
        r = self.client.post("/solicitar-registro", data={"email": email, "lang": lang})
        self.assertEqual(r.status_code, 200, r.text)

    def confirm(self):
        """There is no confirmation step after the form anymore: only the pending notifications are sent."""
        deliver_notifications(self.svc)


class TestNoticeEvents(NoticeFlowBase):

    def test_requesting_the_registration_queues_only_the_email_confirmation(self):
        c = self.make()
        self.assertEqual(c.post("/solicitar-registro", data={"email": "Ana@Example.com"}).status_code, 200)
        notices = self.notices()
        self.assertEqual([n["kind"] for n in notices], [kinds.CONFIRMACION_EMAIL])
        notice = notices[0]
        self.assertEqual(notice["recipient_email"], "ana@example.com")
        self.assertIsNone(notice["request_id"])  # there is no request or admin notification yet
        self.assertEqual(notice["payload"]["expires_hours"], 24)
        self.assertEqual(self.client.get(notice["payload"]["form_url"].replace("http://testserver", "")).status_code, 200)

    def test_the_language_chosen_on_the_home_travels_in_every_client_notice_for_n8n(self):
        c = self.make()
        c.post("/solicitar-registro", data={"email": "ana@example.com", "lang": "en"})
        c.post("/solicitar-registro", data={"email": "beto@example.com", "lang": "es"})
        by_email = {n["recipient_email"]: n for n in self.notices(kind=kinds.CONFIRMACION_EMAIL)}
        self.assertEqual(by_email["ana@example.com"]["payload"]["lang"], "en")
        self.assertEqual(by_email["beto@example.com"]["payload"]["lang"], "es")
        # the form is submitted with Ana's link: the request is in English and so are the notifications that follow
        token = by_email["ana@example.com"]["payload"]["form_url"].split("t=")[1]
        self.submit(t=token)
        self.assertEqual(self.svc.requests.list_requests()[0].lang, "en")  # n8n builds the email text from this field

    def test_each_registration_request_queues_its_own_notice_and_only_the_last_link_keeps_working(self):
        c = self.make()
        c.post("/solicitar-registro", data={"email": "ana@example.com"})
        c.post("/solicitar-registro", data={"email": "ana@example.com"})
        urls = [n["payload"]["form_url"] for n in self.notices(kind=kinds.CONFIRMACION_EMAIL)]
        self.assertEqual(len(urls), 2)
        self.assertNotEqual(urls[0], urls[1])
        codes = [self.client.get(url.replace("http://testserver", "")).status_code for url in urls]
        self.assertEqual(sorted(codes), [200, 404])  # one live link per email: requesting another voids the previous one

    def test_submitting_the_form_queues_only_the_admin_notice(self):
        self.make()
        self.submit()
        by_kind = {n["kind"]: n for n in self.notices()}
        self.assertEqual(set(by_kind), {kinds.SOLICITUD_NUEVA})  # access is free: the client is not notified until the certificate
        admin = by_kind[kinds.SOLICITUD_NUEVA]
        self.assertEqual(admin["recipient_email"], "admin@example.com")
        self.assertEqual(admin["payload"]["title"], "Guion del cortometraje")
        self.assertEqual(admin["payload"]["category_label"], "Guión")
        self.assertIn("/admin/solicitudes/", admin["payload"]["admin_url"])

    def test_without_an_admin_address_nobody_is_notified_when_the_form_is_sent(self):
        self.make(admin_email="")
        self.submit()
        self.assertEqual(self.notices(), [])

    def test_notice_payloads_carry_no_files_or_content(self):
        self.make()
        self.submit(description="descripción privada", document_id="30111222")
        raw = " ".join(str(n["payload"]) for n in self.notices())
        for private in ("contenido", "descripción privada", "30111222", "guion.pdf"):
            self.assertNotIn(private, raw)

    def test_the_status_link_that_later_notices_carry_works(self):
        self.make()
        self.submit()
        request = self.svc.requests.list_requests()[0]
        status = self.client.get(self.svc.events.status_url(request.id).replace("http://testserver", ""))
        self.assertEqual(status.status_code, 200)

    def test_status_link_can_be_rebuilt_without_storing_the_token(self):
        self.make()
        self.submit()
        request = self.svc.requests.list_requests()[0]
        token = self.svc.requests.access_token_for(request.id)
        self.assertEqual(token, self.svc.requests.access_token_for(request.id))
        self.assertNotEqual(token, self.svc.requests.access_token_for("otro-id"))
        with self.engine.connect() as conn:
            stored = conn.execute(text("SELECT access_token_hash FROM evidence_requests")).scalar()
        self.assertNotIn(token, stored)
        self.assertIsNotNone(self.svc.requests.get_with_access_token(request.id, token))

    def test_no_payment_notice_is_queued(self):
        self.make()
        self.submit()
        self.assertEqual([n for n in self.notices() if n["kind"] == kinds.RECEPCION_PAGO], [])
        # the payment is still recorded in the evidence history (it will be activated with subscriptions)
        evidence_id = self.svc.requests.list_requests()[0].evidence_id
        events = {e.event_type for e in self.svc.audit.get_events_for_evidence(evidence_id)}
        self.assertIn("PAYMENT_CONFIRMED", events)

    def test_sending_twice_with_the_same_link_does_not_duplicate_notices(self):
        self.make()
        token = link_token(self.svc)
        self.submit(t=token)
        before = len(self.notices())
        r = self.client.post("/solicitudes", data=dict(FORM, t=token),
                             files=[("files", ("guion.pdf", b"%PDF-1.4 contenido", "application/pdf"))])
        self.assertEqual(r.status_code, 404)
        self.assertEqual(len(self.notices()), before)


class TestBuiltinDelivery(NoticeFlowBase):

    def test_pending_notices_are_sent_and_marked(self):
        self.make()
        self.submit()
        self.client_notice()
        stats = deliver_notifications(self.svc)
        self.assertEqual((stats["lifted"], stats["advanced"]), (2, 2))
        self.assertEqual({m["To"] for m in self.mailer.sent}, {"ana@example.com", "admin@example.com"})
        self.assertEqual(self.svc.notifications.counts(), {"sent": 2})
        self.assertEqual(deliver_notifications(self.svc)["lifted"], 0)

    def test_a_failing_mailer_defers_then_flags_the_notice(self):
        self.make(notification_max_attempts=2)

        def broken(*args, **kwargs):
            raise ConnectionError("SMTP caído")

        self.mailer.send = broken
        self.submit()
        self.client_notice()
        first = deliver_notifications(self.svc)
        self.assertEqual((first["deferred"], first["failed"], first["advanced"]), (2, 0, 0))
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE notifications SET next_attempt_at = now() - interval '1 minute'"))
        second = deliver_notifications(self.svc)
        self.assertEqual((second["deferred"], second["failed"]), (0, 2))
        self.assertEqual(self.svc.notifications.counts(), {"failed": 2})
        self.assertIn("SMTP caído", self.svc.notifications.list(status="failed")[0]["last_error"])

    def test_client_emails_never_name_tools_or_internal_states(self):
        self.make()
        self.submit()
        self.client_notice()
        self.confirm()
        deliver_notifications(self.svc)
        client_mail = "\n".join(m.get_content() for m in self.mailer.sent if m["To"] == "ana@example.com")
        self.assertIn("respondé este correo", client_mail)
        for forbidden in ("OpenTimestamps", "opentimestamps", "manifest", "PENDING", "HASHED", "VERIFIED", "sha256"):
            self.assertNotIn(forbidden, client_mail)

    def test_all_email_kinds_render(self):
        samples = {
            kinds.CONFIRMACION_EMAIL: {"form_url": "http://x/formulario?t=abc", "expires_hours": 24},
            kinds.RECEPCION_SOLICITUD: {"title": "T", "category_label": "Guion", "verification_url": "http://x/v", "status_url": "http://x/s", "expires_hours": 48},
            kinds.RECEPCION_PAGO: {"title": "T", "evidence_id": "PRIMA-1", "payment_label": "Pago recibido", "method": "stripe", "status_url": "http://x/s"},
            kinds.CONFIRMACION_REGISTRO: {"title": "T", "evidence_id": "PRIMA-1", "evidence_hash": "a" * 64, "block_height": 1, "block_date": "", "status_url": "http://x/s"},
            kinds.SOLICITUD_NUEVA: {"client_name": "A", "client_email": "a@b.com", "title": "T", "category_label": "Guion", "admin_url": "http://x/a"},
            kinds.FALLA: {"client_name": "A", "title": "T", "stage_label": "Sellando", "reason": "r", "admin_url": "http://x/a"},
            kinds.ETAPA_ATRASADA: {"client_name": "A", "title": "T", "stage_label": "Sellando", "minutes_in_stage": 20, "deadline_minutes": 15, "admin_url": "http://x/a"},
        }
        self.assertEqual(set(samples), kinds.KINDS)
        for kind, payload in samples.items():
            subject, body = render_email({"kind": kind, "payload": payload, "recipient_name": "Ana"})
            self.assertTrue(subject and body, kind)

    def test_external_delivery_runs_no_local_consumer(self):
        self.make(notification_delivery="external")
        self.assertIsNone(self.svc.delivery)
        self.submit()
        self.client_notice()
        self.svc.worker.run_cycle()
        self.assertEqual(self.svc.notifications.counts(), {"pending": 2})  # waiting for n8n to consume them
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT count(*) FROM worker_runs WHERE process = 'avisos'")).scalar(), 0)


class TestConfirmationAndDelivery(NoticeFlowBase):

    def run_to_certificate(self):
        """Takes an operation to verified and lets the worker certify it."""
        from src.core.state_machine import EvidenceState
        self.submit()
        self.confirm()
        request = self.svc.requests.list_requests()[0]
        eid = request.evidence_id
        ev = self.svc.evidence
        ev.update_proof_ots(eid, b"OTS-PENDING", "PENDING")
        ev.update_evidence_status(eid, EvidenceState.SUBMITTED)
        ev.update_evidence_status(eid, EvidenceState.PENDING_BITCOIN)
        ev.update_proof_ots(eid, b"OTS-ANCHORED", "CONFIRMED", bitcoin_block_height=850000)
        ev.update_evidence_status(eid, EvidenceState.CONFIRMED)
        ev.update_proof_ots(eid, b"OTS-ANCHORED", "VERIFIED", bitcoin_block_height=850000,
                            bitcoin_block_time=datetime(2026, 10, 4, 14, 30, tzinfo=timezone.utc))
        ev.update_evidence_status(eid, EvidenceState.VERIFIED)
        return request, eid

    def stage(self, rid):
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT stage FROM operation_stage WHERE request_id = :i"), {"i": rid}).scalar()

    def test_certifying_queues_the_confirmation_with_the_certificate(self):
        self.make(notification_delivery="external")
        self.submit()
        request = self.svc.requests.list_requests()[0]
        self.assertEqual(self.notices(kind=kinds.CONFIRMACION_REGISTRO), [])
        from src.core.state_machine import EvidenceState
        ev = self.svc.evidence
        eid = request.evidence_id
        for st, ots in ((EvidenceState.SUBMITTED, "PENDING"), (EvidenceState.PENDING_BITCOIN, "PENDING")):
            ev.update_evidence_status(eid, st)
        ev.update_proof_ots(eid, b"OTS", "CONFIRMED", bitcoin_block_height=850000)
        ev.update_evidence_status(eid, EvidenceState.CONFIRMED)
        ev.update_proof_ots(eid, b"OTS", "VERIFIED", bitcoin_block_height=850000, bitcoin_block_time=datetime(2026, 10, 4, 14, 30, tzinfo=timezone.utc))
        ev.update_evidence_status(eid, EvidenceState.VERIFIED)

        self.svc.worker.run_cycle()
        notice = self.notices(kind=kinds.CONFIRMACION_REGISTRO)[0]
        self.assertEqual(notice["recipient_email"], "ana@example.com")
        self.assertEqual(notice["evidence_id"], eid)
        self.assertTrue(notice["payload"]["has_certificate"])
        self.assertEqual(notice["payload"]["block_height"], 850000)
        self.assertEqual(notice["payload"]["evidence_hash"], ev.get_evidence(eid).manifest_hash)
        self.assertEqual(self.stage(request.id), "certificada")  # not delivered yet: nobody consumed the notification

    def test_delivering_the_confirmation_attaches_the_certificate_and_closes_the_operation(self):
        self.make()
        request, eid = self.run_to_certificate()
        self.svc.worker.run_cycle()
        confirmation = [m for m in self.mailer.sent if m["Subject"] == "Tu certificación está lista"]
        self.assertEqual(len(confirmation), 1)
        attachments = list(confirmation[0].iter_attachments())
        self.assertEqual([a.get_filename() for a in attachments], [f"certificate-{eid}.pdf"])
        self.assertEqual(attachments[0].get_content_type(), "application/pdf")
        self.assertTrue(attachments[0].get_content().startswith(b"%PDF-"))
        self.assertEqual(self.stage(request.id), "entregada")

    def test_operation_stays_certificada_until_the_confirmation_is_actually_sent(self):
        self.make()
        request, eid = self.run_to_certificate()

        def broken(*args, **kwargs):
            raise ConnectionError("SMTP caído")

        original = self.mailer.send
        self.mailer.send = broken
        self.svc.worker.run_cycle()
        self.assertEqual(self.stage(request.id), "certificada")
        self.mailer.send = original
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE notifications SET next_attempt_at = now() - interval '1 minute'"))
        self.svc.worker.run_cycle()
        self.assertEqual(self.stage(request.id), "entregada")

    def test_the_confirmation_is_queued_once(self):
        self.make()
        self.run_to_certificate()
        for _ in range(3):
            self.svc.worker.run_cycle()
        self.assertEqual(len(self.notices(kind=kinds.CONFIRMACION_REGISTRO)), 1)

    def test_avisos_process_is_recorded_in_worker_runs(self):
        self.make()
        self.submit()
        self.client_notice()
        self.svc.worker.run_cycle()
        with self.engine.connect() as conn:
            run = conn.execute(text("SELECT lifted, advanced, failed, deferred FROM worker_runs WHERE process = 'avisos'")).one()
        self.assertEqual(tuple(run), (2, 2, 0, 0))


if __name__ == "__main__":
    unittest.main()
