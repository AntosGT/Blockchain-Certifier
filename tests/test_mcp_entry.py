import hashlib
import re
import time
import unittest
from sqlalchemy import text
from db_helper import deliver_notifications
from test_no_storage import TestNoStorage, stored_files

CONTENT = b"%PDF-1.4 propuesta para el cliente"
EMAIL = "ana@example.com"

def body(**over):
    data = {"title": "Propuesta Nike", "category": "propuesta_comercial", "description": "Version enviada",
            "name": "Ana Prueba", "email": EMAIL, "country": "AR", "lang": "es",
            "declaration_version": "1.0", "declaration_accepted": True, "project": "Nike 2026",
            "files": [{"filename": "propuesta.pdf", "size": len(CONTENT), "sha256": hashlib.sha256(CONTENT).hexdigest()}]}
    data.update(over)
    return data

class TestMcpEntry(TestNoStorage):
    """MCP entry: identity by an email confirmed once, registration by fingerprint, source and project; the server does not keep the file."""

    def run(self, result=None):
        if self._testMethodName.startswith("test_mcp_"):
            return unittest.TestCase.run(self, result)
        return None

    def setup_entry(self, **overrides):
        overrides.setdefault("mcp_enabled", True)
        self.make_client(**overrides)
        self.key = self.svc.mcp_keys.create_key(EMAIL)
        self.receipt = self.ask_declaration().json().get("receipt", "")
        return self.client

    def ask_declaration(self, key="default", lang="es"):
        key = self.key if key == "default" else key
        return self.client.get(f"/api/mcp/declaracion?lang={lang}", headers={"Authorization": f"Bearer {key}"} if key else {})

    def go(self, data=None, key="default", headers=None):
        key = self.key if key == "default" else key
        h = {"Authorization": f"Bearer {key}"} if key else {}
        h.update(headers or {})
        payload = dict(body() if data is None else data)
        payload.setdefault("declaration_receipt", self.receipt)  # a case can bring its own (or an empty one) to test the rejection
        return self.client.post("/api/mcp/registros", json=payload, headers=h)

    def rows(self, sql):
        with self.engine.connect() as conn:
            return conn.execute(text(sql)).all()

    # Off and authentication ---------------------------------------------------------

    def test_mcp_everything_is_off_by_default(self):
        c = self.make_client()
        for path, data in (("/api/mcp/registros", body()), ("/api/mcp/conexion", {"email": EMAIL}),
                           ("/api/mcp/conexion/confirmar", {"t": "x"})):
            r = c.post(path, json=data)
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (404, "not_found"), path)
        gone = c.get("/api/mcp/declaracion")
        self.assertEqual((gone.status_code, gone.json()["error"]["code"]), (404, "not_found"))
        self.assertEqual(self.count("evidence_requests"), 0)
        self.assertEqual(self.count("email_verifications"), 0)

    # Declaration: without requesting it first, registering is not possible ----------------------------------

    def test_mcp_the_declaration_is_given_only_with_a_key_and_comes_with_a_receipt(self):
        self.setup_entry()
        self.assertEqual(self.ask_declaration(key=None).status_code, 401)
        self.assertEqual(self.ask_declaration(key="prima_inventada").status_code, 401)
        es, en = self.ask_declaration(lang="es").json(), self.ask_declaration(lang="en").json()
        self.assertEqual((es["version"], en["version"]), ("1.0", "1.0-en"))
        self.assertTrue(es["text"] and es["receipt"] and en["receipt"])
        self.assertNotEqual(es["receipt"], en["receipt"])

    def test_mcp_registering_without_the_receipt_of_the_declaration_is_rejected(self):
        self.setup_entry()
        for receipt in ("", "basura", self.receipt[:-3] + "xyz"):
            r = self.go(body(declaration_receipt=receipt))
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, "declaration.not_shown"), receipt)
        self.assertEqual(self.count("evidence_requests"), 0)
        self.assertEqual(self.go().status_code, 201)

    def test_mcp_a_receipt_of_another_client_or_another_version_is_rejected(self):
        self.setup_entry()
        other_key = self.svc.mcp_keys.create_key("otro@example.com")
        theirs = self.ask_declaration(key=other_key).json()["receipt"]
        r = self.go(body(declaration_receipt=theirs))
        self.assertEqual(r.json()["error"]["code"], "declaration.not_shown")
        en = self.ask_declaration(lang="en").json()
        wrong = self.go(body(declaration_receipt=self.receipt, declaration_version=en["version"], lang="en"))
        self.assertEqual(wrong.json()["error"]["code"], "declaration.not_shown")
        self.assertEqual(self.count("evidence_requests"), 0)

    def test_mcp_an_expired_receipt_is_rejected(self):
        self.setup_entry(mcp_declaration_receipt_minutes=0)
        time.sleep(1.2)
        r = self.go()
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, "declaration.not_shown"))
        self.assertEqual(self.count("evidence_requests"), 0)

    def test_mcp_a_missing_wrong_or_revoked_key_is_rejected(self):
        self.setup_entry()
        for key in (None, "otra-cosa", "prima_inventada"):
            r = self.go(key=key)
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (401, "auth.invalid"), key)
        old = self.key
        self.key = self.svc.mcp_keys.create_key(EMAIL)  # a new key revokes the previous one
        self.assertEqual(self.go(key=old).status_code, 401)
        self.assertEqual(self.go().status_code, 201)
        self.assertEqual(self.count("evidence_requests"), 1)

    def test_mcp_the_key_is_stored_hashed(self):
        self.setup_entry()
        row = self.rows("SELECT key_hash, email FROM mcp_keys")[0]
        self.assertNotEqual(row.key_hash, self.key)
        self.assertEqual((len(row.key_hash), row.email), (64, EMAIL))

    # Connection: the email is confirmed only once -----------------------------------------

    def test_mcp_connecting_sends_the_link_and_confirming_it_gives_a_key_that_works(self):
        c = self.setup_entry()
        r = c.post("/api/mcp/conexion", json={"email": " Beto@Example.com ", "lang": "es"})
        self.assertEqual((r.status_code, r.json()["sent_to"]), (200, "beto@example.com"))
        deliver_notifications(self.svc)
        link = re.search(r"/formulario\?t=([\w-]+)", self.mailer.sent[-1].get_content()).group(1)
        confirmed = c.post("/api/mcp/conexion/confirmar", json={"t": link})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["email"], "beto@example.com")
        key = confirmed.json()["key"]
        self.assertTrue(key.startswith("prima_"))
        own = self.ask_declaration(key=key).json()["receipt"]  # each client uses the receipt of its own key
        self.assertEqual(self.go(body(declaration_receipt=own), key=key).status_code, 201)
        self.assertEqual(self.rows("SELECT email FROM evidence_requests")[0].email, "beto@example.com")
        again = c.post("/api/mcp/conexion/confirmar", json={"t": link})  # the link works only once
        self.assertEqual((again.status_code, again.json()["error"]["code"]), (404, "link.invalid"))

    def test_mcp_the_notice_says_what_the_link_is_for_and_the_portal_notice_does_not(self):
        c = self.setup_entry()
        c.post("/api/mcp/conexion", json={"email": "beto@example.com", "lang": "en"})
        c.post("/api/registro", data={"email": "caro@example.com", "lang": "en"})
        payloads = {r.recipient_email: r.payload for r in self.rows(
            "SELECT recipient_email, payload FROM notifications WHERE kind = 'cliente.confirmacion_email'")}
        self.assertEqual(payloads["beto@example.com"]["purpose"], "mcp")
        self.assertEqual(payloads["beto@example.com"]["lang"], "en")
        self.assertNotIn("purpose", payloads["caro@example.com"])

    def test_mcp_the_certificate_notice_carries_the_project_only_for_the_mcp(self):
        self.setup_entry()
        self.go()
        self.post_api({"declaration_version": "1.0"}, email="otra@example.com")
        for evidence_id in [r.evidence_id for r in self.rows("SELECT evidence_id FROM evidence_requests")]:
            with self.engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO certificates (evidence_id, template_version, layout_sha256, pdf_ref, package_ref, pdf_sha256) "
                    "VALUES (:e, '1.4', 'x', 'p', 'k', 'y')"), {"e": evidence_id})
            self.svc.events.certified(evidence_id)
        payloads = {r.recipient_email: r.payload for r in self.rows(
            "SELECT recipient_email, payload FROM notifications WHERE kind = 'cliente.confirmacion_registro'")}
        self.assertEqual((payloads[EMAIL]["project"], payloads[EMAIL]["source"]), ("Nike 2026", "mcp"))
        self.assertNotIn("project", payloads["otra@example.com"])

    def test_mcp_connecting_validates_the_email_and_limits_by_email(self):
        c = self.setup_entry(submit_rate_per_email_hour=1)
        bad = c.post("/api/mcp/conexion", json={"email": "sin-arroba"})
        self.assertEqual((bad.status_code, bad.json()["error"]["code"]), (400, "email.invalid"))
        self.assertEqual(c.post("/api/mcp/conexion", json={"email": "x@example.com"}).status_code, 200)
        limited = c.post("/api/mcp/conexion", json={"email": "x@example.com"})
        self.assertEqual((limited.status_code, limited.json()["error"]["code"]), (429, "rate.email"))

    def test_mcp_connecting_has_its_own_global_ceiling_and_does_not_use_up_the_web_one(self):
        c = self.setup_entry(submit_global_per_hour=1)
        self.assertEqual(c.post("/api/mcp/conexion", json={"email": "a@example.com"}).status_code, 200)
        limited = c.post("/api/mcp/conexion", json={"email": "b@example.com"})
        self.assertEqual((limited.status_code, limited.json()["error"]["code"]), (429, "rate.email"))
        web = c.post("/api/registro", data={"email": "caro@example.com", "lang": "en"})
        self.assertEqual(web.status_code, 200, web.text)

    def test_mcp_a_body_that_is_not_json_is_rejected_everywhere(self):
        c = self.setup_entry()
        for path in ("/api/mcp/conexion", "/api/mcp/conexion/confirmar", "/api/mcp/registros"):
            r = c.post(path, content=b"no es json", headers={"Authorization": f"Bearer {self.key}"})
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, "body.invalid"), path)

    # Registro ---------------------------------------------------------------------------

    def test_mcp_a_registration_by_fingerprint_follows_the_same_stages_and_keeps_no_file(self):
        c = self.setup_entry()
        r = self.go()
        self.assertEqual(r.status_code, 201, r.text)
        out = r.json()
        self.assertEqual(out["status"], "PROCESSED")
        self.assertTrue(out["evidence_id"])
        req = self.rows("SELECT source, project, email, status FROM evidence_requests")[0]
        self.assertEqual((req.source, req.project, req.email, req.status), ("mcp", "Nike 2026", EMAIL, "PROCESSED"))
        files = self.rows("SELECT filename, sha256, size, storage_reference FROM evidence_files")
        self.assertEqual([(f.filename, f.sha256, f.size, f.storage_reference) for f in files],
                         [("propuesta.pdf", hashlib.sha256(CONTENT).hexdigest(), len(CONTENT), "")])
        self.assertEqual(stored_files(self.settings.storage_root), [])
        self.assertEqual(self.svc.requests.staging_bytes(), 0)
        status = c.get(f"/api/solicitudes/{out['id']}?t={out['access_token']}").json()
        self.assertEqual((status["status"], status["evidence_id"]), ("PROCESSED", out["evidence_id"]))
        self.assertTrue(status["manifest_hash"])

    def test_mcp_the_identity_comes_from_the_key_not_from_the_body(self):
        self.setup_entry()
        self.assertEqual(self.go(body(email="otro@example.com")).status_code, 201)
        self.assertEqual(self.rows("SELECT email FROM evidence_requests")[0].email, EMAIL)

    def test_mcp_the_sealed_manifest_carries_the_fingerprint_sent_by_the_client(self):
        self.setup_entry()
        self.go()
        manifest = self.rows("SELECT manifest_json FROM proofs")[0].manifest_json
        self.assertIn(hashlib.sha256(CONTENT).hexdigest(), manifest)

    def test_mcp_the_portal_still_marks_its_requests_as_portal_without_project(self):
        self.setup_entry()
        self.post_api({"declaration_version": "1.0"})
        req = self.rows("SELECT source, project FROM evidence_requests")[0]
        self.assertEqual((req.source, req.project), ("portal", None))

    def test_mcp_invalid_data_is_rejected_with_a_code_and_creates_nothing(self):
        self.setup_entry(max_video_mb=1)
        cases = {
            "project.required": body(project=""),
            "file.hash_invalid": body(files=[{"filename": "a.pdf", "size": 10, "sha256": "xyz"}]),
            "file.empty": body(files=[{"filename": "a.pdf", "size": 0, "sha256": "a" * 64}]),
            "file.type_not_accepted": body(files=[{"filename": "virus.exe", "size": 10, "sha256": "a" * 64}]),
            "file.video_too_large": body(files=[{"filename": "clip.mp4", "size": 1024 * 1024 + 1, "sha256": "a" * 64}]),
            "content.required": body(files=[]),
            "declaration.required": body(declaration_accepted=False),
            "title.required": body(title=""),
        }
        for code, data in cases.items():
            r = self.go(data)
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, code), code)
        self.assertEqual(self.go().status_code, 201)  # the valid default body does get in
        self.assertEqual(self.count("evidence_requests"), 1)

    def test_mcp_the_same_size_limits_as_the_portal_apply(self):
        self.setup_entry(max_file_mb=1)
        big = body(files=[{"filename": "a.pdf", "size": 1024 * 1024 + 1, "sha256": "a" * 64}])
        self.assertEqual(self.go(big).json()["error"]["code"], "file.too_large")
        ok_video = body(files=[{"filename": "clip.mp4", "size": 1000, "sha256": "b" * 64}])
        self.assertEqual(self.go(ok_video).status_code, 201)

    def test_mcp_the_rate_limit_is_per_key_and_does_not_use_the_per_email_portal_limit(self):
        self.setup_entry(mcp_rate_per_hour=2)
        names = [body(files=[{"filename": f"{i}.pdf", "size": 5, "sha256": f"{i}" * 64}]) for i in range(3)]
        self.assertEqual([self.go(n).status_code for n in names], [201, 201, 429])
        self.assertEqual(self.go(names[0]).json()["error"]["code"], "rate.key")
        other = self.svc.mcp_keys.create_key("otro@example.com")
        other_receipt = self.ask_declaration(key=other).json()["receipt"]
        self.assertEqual(self.go(dict(names[1], declaration_receipt=other_receipt), key=other).status_code, 201)

if __name__ == "__main__":
    unittest.main()
