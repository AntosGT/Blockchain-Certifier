import unittest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import text
from src.api.app import create_app
from src.core import notifications as kinds
from src.core.state_machine import EvidenceState
from test_notifications import NoticeFlowBase

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class InternalBase(NoticeFlowBase):

    def setUp(self):
        super().setUp()
        self.make(notification_delivery="external", internal_api_token=TOKEN)

    def get(self, path, **kw):
        return self.client.get(path, headers=AUTH, **kw)

    def post(self, path, **kw):
        return self.client.post(path, headers=AUTH, **kw)

    def submit(self, **data):
        """Submitted form plus a registration request: two pending notifications, the admin one and the link to the client."""
        location = super().submit(**data)
        self.client_notice()
        return location

    def first_notice(self, kind=kinds.CONFIRMACION_EMAIL):
        return next(n for n in self.notices() if n["kind"] == kind)


class TestInternalAuth(InternalBase):

    def test_every_route_rejects_a_missing_or_wrong_token(self):
        self.submit()
        nid = self.first_notice()["id"]
        routes = [("get", "/internal/salud"), ("get", "/internal/avisos/pendientes"),
                  ("post", f"/internal/avisos/{nid}/enviado"), ("post", f"/internal/avisos/{nid}/fallido"),
                  ("get", f"/internal/avisos/{nid}/adjunto"), ("get", f"/internal/avisos/{nid}/paquete")]
        for method, path in routes:
            for headers in ({}, {"Authorization": "Bearer incorrecto"}, {"Authorization": f"Basic {TOKEN}"}, {"Authorization": TOKEN}):
                r = getattr(self.client, method)(path, headers=headers)
                self.assertEqual(r.status_code, 401, (path, headers))
        self.assertEqual(self.svc.notifications.counts(), {"pending": 2})  # nothing was touched

    def test_the_api_does_not_exist_without_a_configured_token(self):
        self.make(notification_delivery="external", internal_api_token="")
        for path in ("/internal/salud", "/internal/avisos/pendientes"):
            self.assertEqual(self.client.get(path, headers=AUTH).status_code, 404)
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_health_with_the_right_token(self):
        self.assertEqual(self.get("/internal/salud").json(), {"status": "ok"})

    def test_internal_routes_are_not_indexable_or_cacheable(self):
        r = self.get("/internal/salud")
        self.assertEqual(r.headers["cache-control"], "no-store")


class TestPendingNotices(InternalBase):

    def test_lists_pending_notices_in_the_agreed_shape(self):
        self.submit()
        body = self.get("/internal/avisos/pendientes").json()
        self.assertEqual(body["count"], 2)
        item = next(i for i in body["items"] if i["kind"] == kinds.CONFIRMACION_EMAIL)
        self.assertEqual(item["event_id"], self.first_notice()["id"])
        self.assertEqual((item["channel"], item["attempts"]), ("email", 0))
        self.assertEqual(item["to"]["email"], "ana@example.com")
        self.assertIn("form_url", item["data"])
        self.assertEqual(item["data"]["lang"], "es")
        self.assertIsNone(item["attachment_url"])
        self.assertEqual(set(item), {"event_id", "kind", "channel", "attempts", "created_at", "to", "data", "attachment_url", "package_url"})

    def test_a_second_consumer_does_not_get_the_same_notices(self):
        self.submit()
        self.assertEqual(self.get("/internal/avisos/pendientes").json()["count"], 2)
        self.assertEqual(self.get("/internal/avisos/pendientes").json()["count"], 0)

    def test_unconfirmed_notices_come_back_after_the_lease(self):
        self.submit()
        self.get("/internal/avisos/pendientes")
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE notifications SET next_attempt_at = now() - interval '1 minute'"))
        self.assertEqual(self.get("/internal/avisos/pendientes").json()["count"], 2)

    def test_the_batch_size_is_limited(self):
        for i in range(3):
            self.submit(email=f"cliente{i}@example.com", title=f"Operación {i}")
        self.assertEqual(self.get("/internal/avisos/pendientes?limite=2").json()["count"], 2)
        self.assertEqual(self.get("/internal/avisos/pendientes?limite=0").json()["count"], 1)  # minimum 1
        self.assertEqual(self.get("/internal/avisos/pendientes?limite=100000").json()["count"], 3)  # capped, no error

    def test_no_secrets_travel_in_the_notices(self):
        self.submit()
        raw = self.get("/internal/avisos/pendientes").text
        for secret in (TOKEN, "access_token_hash", "verification_token_hash", self.settings.session_secret):
            self.assertNotIn(secret, raw)


class TestConfirmingNotices(InternalBase):

    def test_sent_is_idempotent_and_closes_the_notice(self):
        self.submit()
        nid = self.first_notice()["id"]
        self.assertEqual(self.post(f"/internal/avisos/{nid}/enviado").json(), {"ok": True})
        self.assertEqual(self.post(f"/internal/avisos/{nid}/enviado").json(), {"ok": True})
        self.assertEqual(self.svc.notifications.get(nid)["status"], "sent")
        self.assertEqual(self.get("/internal/avisos/pendientes").json()["count"], 1)  # only the admin one is left

    def test_unknown_or_malformed_ids_give_404(self):
        for nid in ("no-es-un-uuid", "00000000-0000-0000-0000-000000000000"):
            self.assertEqual(self.post(f"/internal/avisos/{nid}/enviado").status_code, 404)
            self.assertEqual(self.post(f"/internal/avisos/{nid}/fallido", json={"error": "x"}).status_code, 404)
            self.assertEqual(self.get(f"/internal/avisos/{nid}/adjunto").status_code, 404)

    def test_failed_reschedules_and_eventually_flags_the_notice(self):
        self.make(notification_delivery="external", internal_api_token=TOKEN, notification_max_attempts=2)
        self.submit()
        nid = self.first_notice()["id"]
        first = self.post(f"/internal/avisos/{nid}/fallido", json={"error": "Gmail rechazó el envío"})
        self.assertEqual(first.json(), {"ok": True, "status": "pending"})
        notice = self.svc.notifications.get(nid)
        self.assertEqual((notice["attempts"], notice["last_error"]), (1, "Gmail rechazó el envío"))
        second = self.post(f"/internal/avisos/{nid}/fallido", json={"error": "otra vez"})
        self.assertEqual(second.json(), {"ok": True, "status": "failed"})

    def test_failed_without_a_body_still_records_the_attempt(self):
        self.submit()
        nid = self.first_notice()["id"]
        self.assertEqual(self.post(f"/internal/avisos/{nid}/fallido").status_code, 200)
        self.assertEqual(self.svc.notifications.get(nid)["last_error"], "Sin detalle")

    def test_a_sent_notice_cannot_be_failed_afterwards(self):
        self.submit()
        nid = self.first_notice()["id"]
        self.post(f"/internal/avisos/{nid}/enviado")
        r = self.post(f"/internal/avisos/{nid}/fallido", json={"error": "tarde"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.svc.notifications.get(nid)["status"], "sent")


class TestAttachment(InternalBase):

    def certified_notice(self):
        self.submit()
        eid = self.svc.requests.list_requests()[0].evidence_id
        ev = self.svc.evidence
        for st in (EvidenceState.SUBMITTED, EvidenceState.PENDING_BITCOIN):
            ev.update_evidence_status(eid, st)
        ev.update_proof_ots(eid, b"OTS", "CONFIRMED", bitcoin_block_height=850000)
        ev.update_evidence_status(eid, EvidenceState.CONFIRMED)
        ev.update_proof_ots(eid, b"OTS", "VERIFIED", bitcoin_block_height=850000, bitcoin_block_time=datetime(2026, 10, 4, 14, 30, tzinfo=timezone.utc))
        ev.update_evidence_status(eid, EvidenceState.VERIFIED)
        self.svc.worker.run_cycle()
        return self.first_notice(kinds.CONFIRMACION_REGISTRO), eid

    def test_the_confirmation_points_to_the_certificate_and_it_can_be_downloaded(self):
        notice, eid = self.certified_notice()
        item = next(i for i in self.get("/internal/avisos/pendientes?limite=50").json()["items"] if i["kind"] == kinds.CONFIRMACION_REGISTRO)
        self.assertEqual(item["attachment_url"], f"/internal/avisos/{notice['id']}/adjunto")
        r = self.get(item["attachment_url"])
        self.assertEqual((r.status_code, r.headers["content-type"]), (200, "application/pdf"))
        self.assertTrue(r.content.startswith(b"%PDF-"))
        self.assertIn(eid, r.headers["content-disposition"])

    def test_the_confirmation_also_points_to_the_package_and_it_can_be_downloaded(self):
        notice, eid = self.certified_notice()
        item = next(i for i in self.get("/internal/avisos/pendientes?limite=50").json()["items"] if i["kind"] == kinds.CONFIRMACION_REGISTRO)
        self.assertEqual(item["package_url"], f"/internal/avisos/{notice['id']}/paquete")
        r = self.get(item["package_url"])
        self.assertEqual((r.status_code, r.headers["content-type"]), (200, "application/zip"))
        self.assertTrue(r.content.startswith(b"PK"))
        self.assertIn(eid, r.headers["content-disposition"])

    def test_other_notices_have_no_attachment(self):
        self.submit()
        nid = self.first_notice()["id"]
        self.assertEqual(self.get(f"/internal/avisos/{nid}/adjunto").status_code, 404)
        self.assertEqual(self.get(f"/internal/avisos/{nid}/paquete").status_code, 404)


if __name__ == "__main__":
    unittest.main()
