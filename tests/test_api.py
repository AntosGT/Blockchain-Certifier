import unittest
from db_helper import link_token
from test_portal import FORM, PDF, TestPortal

class TestPublicApi(TestPortal):
    """Portal JSON API: what the static frontend consumes. Same setup as TestPortal."""

    # The page tests are inherited; only the API ones run here
    def run(self, result=None):
        if self._testMethodName.startswith("test_api_"):
            return super().run(result)
        return None

    def post_api(self, data=None, files=None, lang="es", email="ana@example.com"):
        data = dict(FORM, **(data or {}))
        if "t" not in data:  # creating a new link voids the previous ones for the same email
            data["t"] = link_token(self.svc, email, lang)
        return self.client.post("/api/solicitudes", data=data, files=PDF if files is None else files)

    def test_api_config_gives_the_form_data_in_each_language(self):
        c = self.make_client()
        en = c.get("/api/config?lang=en").json()
        es = c.get("/api/config?lang=es").json()
        self.assertEqual(en["lang"], "en")
        self.assertEqual(en["declaration"]["version"], "1.0-en")
        self.assertEqual(es["declaration"]["version"], "1.0")
        self.assertIn("Script", [x["label"] for x in en["categories"]])
        self.assertIn("Guión", [x["label"] for x in es["categories"]])
        self.assertIn("Spain", [x["name"] for x in en["countries"]])
        self.assertIn("España", [x["name"] for x in es["countries"]])
        self.assertEqual(len(en["countries"]), len(es["countries"]))
        for key in ("max_files", "max_file_mb", "max_video_mb", "allowed_extensions", "link_hours", "turnstile_site_key", "form_token"):
            self.assertIn(key, en)
        self.assertEqual(en["max_video_mb"], 25)
        from src.core.i18n import MESSAGES
        for lang, text in MESSAGES["form.video_limit"].items():
            self.assertIn("{max_video_mb}", text)
            self.assertIn("480p", text)
            for forbidden in ("minut", "duraci", "durat", "suscrip", "subscri", "MCP", "OpenTimestamps"):
                self.assertNotIn(forbidden, text, lang)  # no duration, prices or internal names
        self.assertEqual(c.get("/api/config?lang=xx").json()["lang"], "en")  # unknown language: English

    def test_api_i18n_gives_every_text_of_the_language(self):
        from src.core.i18n import MESSAGES
        c = self.make_client()
        en, es = c.get("/api/i18n?lang=en").json(), c.get("/api/i18n?lang=es").json()
        self.assertEqual(set(en), set(MESSAGES))
        self.assertEqual(en["form.submit"], "Submit request")
        self.assertEqual(es["form.submit"], "Enviar solicitud")

    def test_api_registration_sends_the_link_and_answers_in_json(self):
        c = self.make_client()
        r = c.post("/api/registro", data={"email": " Ana@Example.com ", "lang": "en"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True, "sent_to": "ana@example.com", "link_hours": 24})
        self.assertIn("/formulario?t=", self.emailed_link_text())

    def emailed_link_text(self):
        from db_helper import deliver_notifications
        deliver_notifications(self.svc)
        return self.mailer.sent[-1].get_content()

    def test_api_registration_errors_are_translated_and_carry_a_code(self):
        c = self.make_client(submit_rate_per_hour=2)
        bad = c.post("/api/registro", data={"email": "sin-arroba", "lang": "en"})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.json()["error"], {"code": "email.invalid", "message": "The email is not valid."})
        es = c.post("/api/registro", data={"email": "sin-arroba", "lang": "es"})
        self.assertEqual(es.json()["error"]["message"], "El email no es válido.")
        limited = c.post("/api/registro", data={"email": "a@example.com", "lang": "en"})
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()["error"]["code"], "rate.ip")

    def test_api_form_opens_with_the_link_and_gives_email_and_language(self):
        c = self.make_client()
        ok = c.get(f"/api/formulario?t={link_token(self.svc, 'ana@example.com', 'en')}")
        self.assertEqual((ok.status_code, ok.json()), (200, {"email": "ana@example.com", "lang": "en"}))
        gone = c.get("/api/formulario?t=nada")
        self.assertEqual(gone.status_code, 404)
        self.assertEqual(gone.json()["error"]["code"], "link.invalid")
        self.assertIn("does not exist", gone.json()["error"]["message"])  # default language: English

    def test_api_submit_creates_the_request_and_returns_the_status_path(self):
        c = self.make_client()
        r = self.post_api({"declaration_version": "1.0-en"}, lang="en")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["status_path"].startswith(f"/solicitudes/{body['id']}?t="))
        status = c.get(f"/api/solicitudes/{body['id']}?t={body['access_token']}").json()
        self.assertEqual((status["lang"], status["status"], status["title"]), ("en", "PROCESSED", "Guion del cortometraje"))
        self.assertEqual(status["declaration_version"], "1.0-en")
        self.assertEqual(status["category_label"], "Script")
        self.assertTrue(status["evidence_id"] and status["manifest_hash"])
        self.assertEqual(self.count("evidences"), 1)

    def test_api_submit_errors_come_translated_with_their_code(self):
        self.make_client()
        en = self.post_api({"title": "", "declaration_version": "1.0-en"}, lang="en")
        self.assertEqual((en.status_code, en.json()["error"]["code"], en.json()["error"]["message"]),
                         (400, "title.required", "The title is required."))
        es = self.post_api({"title": ""}, lang="es")
        self.assertEqual(es.json()["error"]["message"], "El título es obligatorio.")
        bad = self.post_api({"declaration_version": "1.0-en"}, files=[("files", ("virus.exe", b"MZ", "application/octet-stream"))], lang="en")
        self.assertEqual(bad.json()["error"]["code"], "file.type_not_accepted")
        self.assertEqual(self.count("evidence_requests"), 0)

    def test_api_submit_without_a_valid_link_is_404_and_a_used_link_cannot_be_reused(self):
        self.make_client()
        self.assertEqual(self.post_api({"t": "nada"}).status_code, 404)
        token = link_token(self.svc)
        self.assertEqual(self.post_api({"t": token}).status_code, 200)
        self.assertEqual(self.post_api({"t": token}).status_code, 404)
        self.assertEqual(self.count("evidences"), 1)

    def test_api_status_needs_the_private_token(self):
        c = self.make_client()
        body = self.post_api().json()
        self.assertEqual(c.get(f"/api/solicitudes/{body['id']}").status_code, 404)
        self.assertEqual(c.get(f"/api/solicitudes/{body['id']}?t=incorrecto").status_code, 404)
        self.assertEqual(c.get("/api/solicitudes/no-es-un-uuid?t=x").status_code, 404)
        self.assertEqual(c.get(f"/api/solicitudes/{body['id']}?t={body['access_token']}").status_code, 200)

    def test_api_answers_are_never_cached(self):
        c = self.make_client()
        self.assertEqual(c.get("/api/config").headers["cache-control"], "no-store")

if __name__ == "__main__":
    unittest.main()
