import os
import shutil
import tempfile
import time
import unittest
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlalchemy import text
from db_helper import PostgresTestCase
from test_portal import FORM, fake_submit
from src.api.app import create_app
from src.api.services import build_services
from src.core.bot_protection import FormGuard, TurnstileVerifier
from src.core.config import Settings
from src.core.control import ControlProcess
from src.core.mailer import OutboxMailer


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeVerifier:
    """Accepts only the token 'ok'. Stores the IP it was queried with."""

    def __init__(self):
        self.calls = []

    def verify(self, token, remote_ip=None):
        self.calls.append((token, remote_ip))
        return token == "ok"


class TestTurnstileVerifier(unittest.TestCase):

    def test_accepts_only_an_explicit_success(self):
        ok = TurnstileVerifier("secret", post=lambda url, data, timeout: FakeResponse({"success": True}))
        self.assertTrue(ok.verify("token", "203.0.113.5"))
        no = TurnstileVerifier("secret", post=lambda url, data, timeout: FakeResponse({"success": False}))
        self.assertFalse(no.verify("token"))
        vague = TurnstileVerifier("secret", post=lambda url, data, timeout: FakeResponse({}))
        self.assertFalse(vague.verify("token"))

    def test_fails_closed_when_cloudflare_does_not_answer(self):
        def boom(url, data, timeout):
            raise TimeoutError("sin respuesta")
        self.assertFalse(TurnstileVerifier("secret", post=boom).verify("token"))

    def test_rejects_an_empty_or_oversized_token_without_calling_cloudflare(self):
        calls = []
        verifier = TurnstileVerifier("secret", post=lambda url, data, timeout: calls.append(1) or FakeResponse({"success": True}))
        self.assertFalse(verifier.verify(""))
        self.assertFalse(verifier.verify("x" * 3000))
        self.assertEqual(calls, [])

    def test_sends_the_secret_the_token_and_the_visitor_ip(self):
        sent = {}
        def capture(url, data, timeout):
            sent.update(data)
            return FakeResponse({"success": True})
        TurnstileVerifier("my-secret", post=capture).verify("tok", "198.51.100.7")
        self.assertEqual(sent, {"secret": "my-secret", "response": "tok", "remoteip": "198.51.100.7"})


class TestFormGuard(unittest.TestCase):

    def test_a_fresh_token_is_refused_until_the_minimum_time_passes(self):
        guard = FormGuard("s" * 32, min_seconds=60)
        self.assertFalse(guard.check(guard.issue(), ""))
        self.assertTrue(FormGuard("s" * 32, min_seconds=0).check(FormGuard("s" * 32).issue(), ""))

    def test_the_honeypot_must_be_empty(self):
        guard = FormGuard("s" * 32, min_seconds=0)
        self.assertFalse(guard.check(guard.issue(), "http://spam.example"))

    def test_tampered_missing_or_foreign_tokens_are_refused(self):
        guard = FormGuard("s" * 32, min_seconds=0)
        token = guard.issue()
        self.assertFalse(guard.check(token + "x", ""))
        self.assertFalse(guard.check("", ""))
        self.assertFalse(guard.check(FormGuard("otro-secreto-distinto" * 2, min_seconds=0).issue(), ""))

    def test_an_old_form_is_refused(self):
        guard = FormGuard("s" * 32, min_seconds=0, max_age_seconds=1)
        token = guard.issue()
        time.sleep(2.1)
        self.assertFalse(guard.check(token, ""))


class BotBase(PostgresTestCase):

    def make_client(self, verifier=None, **overrides):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        cfg = dict(_env_file=None, storage_root=os.path.join(self.tmp, "storage"), outbox_dir=os.path.join(self.tmp, "outbox"),
                   public_base_url="http://testserver")
        cfg.update(overrides)
        self.settings = Settings(**cfg)
        self.mailer = OutboxMailer(self.settings.outbox_dir)
        self.svc = build_services(self.settings, engine=self.engine, mailer=self.mailer, submit_fn=fake_submit)
        self.client = TestClient(create_app(self.settings, self.svc, turnstile=verifier))
        return self.client

    def post(self, data=None, headers=None):
        """Registration request (the step that sends the email and carries the bot defenses)."""
        return self.client.post("/solicitar-registro", data=dict(FORM, **(data or {})), headers=headers or {})

    def count(self, table):
        with self.engine.connect() as conn:
            return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()


class TestTurnstileOnTheForm(BotBase):

    KEYS = dict(turnstile_site_key="site-key", turnstile_secret_key="secret-key")

    def test_a_submission_without_a_valid_challenge_is_refused_and_stores_nothing(self):
        verifier = FakeVerifier()
        self.make_client(verifier, **self.KEYS)
        self.assertEqual(self.post().status_code, 400)
        self.assertEqual(self.post({"cf-turnstile-response": "falso"}).status_code, 400)
        self.assertEqual(self.count("email_verifications"), 0)

    def test_a_valid_challenge_lets_the_request_through(self):
        verifier = FakeVerifier()
        self.make_client(verifier, **self.KEYS)
        r = self.post({"cf-turnstile-response": "ok"}, headers={"CF-Connecting-IP": "203.0.113.9"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.count("email_verifications"), 1)

    def test_the_visitor_ip_is_sent_to_cloudflare_only_behind_a_trusted_proxy(self):
        verifier = FakeVerifier()
        self.make_client(verifier, proxy_ip_header="CF-Connecting-IP", trusted_proxy_ips="testclient", **self.KEYS)
        self.post({"cf-turnstile-response": "ok"}, headers={"CF-Connecting-IP": "203.0.113.9"})
        self.assertEqual(verifier.calls[-1], ("ok", "203.0.113.9"))

    def test_the_form_shows_the_widget_and_the_csp_allows_only_turnstile(self):
        self.make_client(FakeVerifier(), **self.KEYS)
        page = self.client.get("/")
        self.assertIn('data-sitekey="site-key"', page.text)
        self.assertIn("challenges.cloudflare.com/turnstile/v0/api.js", page.text)
        csp = page.headers["content-security-policy"]
        self.assertIn("script-src 'self' https://challenges.cloudflare.com", csp)
        self.assertIn("frame-src https://challenges.cloudflare.com", csp)

    def test_without_turnstile_keys_there_is_no_widget_and_the_csp_stays_closed(self):
        self.make_client()
        page = self.client.get("/")
        self.assertNotIn("cf-turnstile", page.text)
        self.assertNotIn("challenges.cloudflare.com", page.headers["content-security-policy"])

    def test_the_admin_login_also_asks_for_the_challenge(self):
        self.make_client(FakeVerifier(), **self.KEYS)
        self.assertIn('data-sitekey="site-key"', self.client.get("/admin/login").text)
        r = self.client.post("/admin/login", data={"email": "a@example.com", "password": "x" * 12})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/admin/login", data={"email": "a@example.com", "password": "x" * 12, "cf-turnstile-response": "ok"})
        self.assertEqual(r.status_code, 401)  # the challenge passed; the credentials do not exist


class TestFormGuardOnTheForm(BotBase):

    def form_token(self):
        page = self.client.get("/").text
        marker = 'name="form_token" value="'
        return page.split(marker)[1].split('"')[0]

    def test_a_submission_with_a_fresh_token_passes(self):
        self.make_client(form_guard_enabled=True, form_min_seconds=0)
        self.assertEqual(self.post({"form_token": self.form_token()}).status_code, 200)

    def test_a_missing_or_tampered_token_is_refused(self):
        self.make_client(form_guard_enabled=True, form_min_seconds=0)
        self.assertEqual(self.post().status_code, 400)
        self.assertEqual(self.post({"form_token": self.form_token() + "x"}).status_code, 400)
        self.assertEqual(self.count("email_verifications"), 0)

    def test_a_filled_honeypot_is_refused(self):
        self.make_client(form_guard_enabled=True, form_min_seconds=0)
        self.assertEqual(self.post({"form_token": self.form_token(), "hp_trap_field": "http://spam.example"}).status_code, 400)

    def test_a_submission_that_is_too_fast_is_refused(self):
        self.make_client(form_guard_enabled=True, form_min_seconds=30)
        self.assertEqual(self.post({"form_token": self.form_token()}).status_code, 400)

    def test_the_trap_field_is_in_the_form_and_invisible_to_people(self):
        self.make_client(form_guard_enabled=True)
        page = self.client.get("/").text
        self.assertIn('name="hp_trap_field"', page)
        self.assertIn('tabindex="-1"', page)


class TestAbuseLimits(BotBase):

    def test_one_address_cannot_be_flooded_with_emails(self):
        self.make_client(submit_rate_per_email_hour=2)
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.post().status_code, 429)
        self.assertEqual(self.count("email_verifications"), 2)
        self.assertEqual(self.post({"email": "otra@example.com"}).status_code, 200)

    def test_there_is_a_global_ceiling_per_hour(self):
        self.make_client(submit_global_per_hour=2)
        self.assertEqual(self.post({"email": "a@example.com"}).status_code, 200)
        self.assertEqual(self.post({"email": "b@example.com"}).status_code, 200)
        self.assertEqual(self.post({"email": "c@example.com"}).status_code, 429)

    def test_a_rejected_request_does_not_spend_the_email_quota(self):
        self.make_client(submit_rate_per_email_hour=1)
        self.assertEqual(self.post({"email": "sin-arroba"}).status_code, 400)
        self.assertEqual(self.post().status_code, 200)


class TestFavicon(BotBase):

    def test_every_page_links_the_icons_and_they_are_served_and_cacheable(self):
        self.make_client()
        for path in ("/", "/admin/login"):
            page = self.client.get(path).text
            self.assertIn('href="/favicon.ico"', page)
            self.assertIn('href="/static/apple-touch-icon.png"', page)
        for path, kind in (("/favicon.ico", "icon"), ("/static/favicon-32.png", "png"), ("/static/icon-192.png", "png"),
                           ("/static/apple-touch-icon.png", "png")):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertEqual(r.headers["cache-control"], "public, max-age=86400")
            self.assertTrue(r.content.startswith(b"\x89PNG") if kind == "png" else r.content[:4] == b"\x00\x00\x01\x00", path)

    def test_pages_with_tokens_are_still_never_cached(self):
        self.make_client()
        self.assertEqual(self.client.get("/").headers["cache-control"], "no-store")

    def test_the_static_folder_does_not_expose_anything_outside_itself(self):
        self.make_client()
        self.assertEqual(self.client.get("/static/../app.py").status_code, 404)
        self.assertEqual(self.client.get("/static/nada.png").status_code, 404)


class TestExpiredFilesPurge(BotBase):

    def staged_request(self):
        """A request (from the previous flow, waiting for the email) with its file in staging."""
        rid = str(uuid4())
        path = self.svc.requests.stage_path(rid, "guion.pdf")
        with open(path, "wb") as f:
            f.write(b"%PDF-1.4 contenido")
        fields = dict(FORM, email="ana@example.com", document_id=None)
        self.svc.requests.create(rid, fields, [{"filename": "guion.pdf", "size": 18, "storage_path": path}], 48)
        return rid

    def submit_and_expire(self, hours_ago):
        rid = self.staged_request()
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE evidence_requests SET status = 'EXPIRED', updated_at = now() - make_interval(hours => :h) "
                              "WHERE id = :i"), {"h": hours_ago, "i": rid})
        return rid

    def control(self, retention_hours=168):
        return ControlProcess(self.engine, self.svc.events, {}, staging_root=self.svc.requests.staging_root,
                              purge_after_hours=retention_hours)

    def run_control(self, control):
        control.run({}, {"lifted": 0, "advanced": 0, "failed": 0, "deferred": 0})

    def staged(self, rid):
        return os.path.isdir(os.path.join(self.svc.requests.staging_root, rid))

    def test_files_of_old_expired_requests_are_deleted_and_recent_ones_are_kept(self):
        self.make_client()
        old = self.submit_and_expire(hours_ago=200)
        recent = self.submit_and_expire(hours_ago=2)
        self.assertTrue(self.staged(old) and self.staged(recent))
        self.run_control(self.control())
        self.assertFalse(self.staged(old))
        self.assertTrue(self.staged(recent))

    def test_pending_requests_never_lose_their_files(self):
        self.make_client()
        rid = self.staged_request()
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE evidence_requests SET updated_at = now() - interval '999 hours' WHERE id = :i"), {"i": rid})
        self.run_control(self.control())
        self.assertTrue(self.staged(rid))


if __name__ == "__main__":
    unittest.main()
