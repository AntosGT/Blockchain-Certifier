import os
import re
import shutil
import tempfile
import unittest
from fastapi.testclient import TestClient
from sqlalchemy import text
from db_helper import PostgresTestCase, deliver_notifications, link_token
from src.api.app import create_app
from src.api.services import build_services
from src.core.config import Settings
from src.core.mailer import OutboxMailer
from src.core.state_machine import EvidenceState

def fake_submit(sha256_hex):
    return b"FAKE-OTS-" + sha256_hex.encode()

FORM = {
    "title": "Guion del cortometraje", "category": "guion", "description": "Primera versión",
    "name": "Ana Prueba", "email": "ana@example.com", "country": "ar", "document_id": "",
    "declaration_version": "1.0", "declaration_accepted": "1",
}

PDF = [("files", ("guion.pdf", b"%PDF-1.4 contenido del guion", "application/pdf"))]

class TestPortal(PostgresTestCase):

    def make_client(self, **overrides):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        cfg = dict(_env_file=None, storage_root=os.path.join(self.tmp, "storage"), outbox_dir=os.path.join(self.tmp, "outbox"),
                   public_base_url="http://testserver")
        cfg.update(overrides)
        self.settings = Settings(**cfg)
        self.mailer = OutboxMailer(self.settings.outbox_dir)
        self.svc = build_services(self.settings, engine=self.engine, mailer=self.mailer, submit_fn=fake_submit)
        self.client = TestClient(create_app(self.settings, self.svc))
        return self.client

    def post_request(self, data=None, files=None):
        data = dict(FORM, **(data or {}))
        if "t" not in data:  # a new link voids the previous ones for the same email: only created if the test does not bring its own
            data["t"] = link_token(self.svc, data["email"])
        if files is None:
            files = PDF
        return self.client.post("/solicitudes", data=data, files=files, follow_redirects=False)

    def count(self, table):
        with self.engine.connect() as conn:
            return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()

    def submit_ok(self):
        r = self.post_request()
        self.assertEqual(r.status_code, 303, r.text)
        deliver_notifications(self.svc)
        return r.headers["location"]

    def register(self, email="ana@example.com", lang="es", **kw):
        return self.client.post("/solicitar-registro", data={"email": email, "lang": lang}, **kw)

    def emailed_link(self):
        deliver_notifications(self.svc)
        body = self.mailer.sent[-1].get_content()
        return re.search(r"/formulario\?t=([\w-]+)", body).group(1)

    # Pages -----------------------------------------------------------------------

    def test_home_page_offers_to_request_the_registration_and_never_mentions_the_test_mode(self):
        c = self.make_client()
        r = c.get("/es")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Verificar email y acceder al formulario", r.text)
        self.assertNotIn("Solicitar registro", r.text)
        self.assertIn('action="/solicitar-registro"', r.text)
        self.assertNotIn('name="title"', r.text)  # the full form is not visible without the link
        for text_that_must_not_show in ("prueba interna", "Modo de prueba", "bypass", "Sin cargo", "Precio"):
            self.assertNotIn(text_that_must_not_show, r.text)
        self.assertEqual(c.get("/salud").json(), {"status": "ok"})

    def test_the_landing_animates_its_sections_with_a_script_file_the_csp_allows(self):
        c = self.make_client()
        js = c.get("/static/reveal.js")
        self.assertEqual(js.status_code, 200)
        self.assertIn("IntersectionObserver", js.text)
        self.assertIn("prefers-reduced-motion", js.text)  # respeta "reducir movimiento"
        for path in ("/", "/es"):
            r = c.get(path)
            self.assertIn('<script src="/static/reveal.js?v=4"></script>', r.text)
            self.assertIn(".reveal-ready .reveal", r.text)
            self.assertIn("@keyframes hero-rise", r.text)  # the hero text comes in on load, with CSS only
            self.assertNotIn("<script>", r.text)  # no inline scripts: the CSP does not allow them
            self.assertIn("default-src 'self'", r.headers["content-security-policy"])
            for selector_class in ('class="about section-pad"', 'class="how section-pad"', 'class="form-col"', 'id="donar"'):
                self.assertIn(selector_class, r.text, path)  # the blocks the script animates exist in both landings

    def test_form_page_shows_declaration_and_the_confirmed_email(self):
        c = self.make_client()
        r = c.get(f"/formulario?t={link_token(self.svc)}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Declaro que presento este contenido", r.text)
        self.assertIn("ana@example.com", r.text)
        for text_that_must_not_show in ("prueba interna", "Modo de prueba", "bypass", "Sin cargo", "Precio"):
            self.assertNotIn(text_that_must_not_show, r.text)

    def test_country_is_a_dropdown_with_all_countries(self):
        c = self.make_client()
        r = c.get(f"/formulario?t={link_token(self.svc)}")
        self.assertIn('<select id="country" name="country" required data-dropdown>', r.text)
        self.assertIn('<option value="AR" >Argentina</option>', r.text)
        self.assertEqual(len(re.findall(r'<option value="[A-Z]{2}"', r.text)), 249)

    def test_client_never_sees_internal_technology_or_state_names(self):
        c = self.make_client()
        pages = [c.get("/").text]
        pages.append(self.register().text)
        pages.append(c.get(f"/formulario?t={self.emailed_link()}").text)
        location = self.submit_ok()
        pages.append(c.get(location).text)
        pages.append(c.get("/formulario?t=nada").text)
        self.svc.worker.run_cycle()
        pages.append(c.get(location).text)
        deliver_notifications(self.svc)
        mails = "\n".join(m.get_content() for m in self.mailer.sent)
        for page in pages + [mails]:
            for forbidden in ("OpenTimestamps", "opentimestamps", "manifest", "PENDING_EMAIL", "PROCESSED", "HASHED", "sha256"):
                self.assertNotIn(forbidden, page)
        evidence_id = self.svc.requests.list_requests()[0].evidence_id
        self.assertIn(self.svc.evidence.get_evidence(evidence_id).manifest_hash, pages[3])  # it does see its own hash

    def test_security_headers(self):
        r = self.make_client().get("/")
        self.assertEqual(r.headers["referrer-policy"], "no-referrer")
        self.assertEqual(r.headers["x-frame-options"], "DENY")
        self.assertIn("default-src 'self'", r.headers["content-security-policy"])

    def test_there_is_a_spanish_and_an_english_home_that_link_to_each_other(self):
        c = self.make_client()
        es, en = c.get("/es"), c.get("/")
        self.assertEqual((es.status_code, en.status_code), (200, 200))
        self.assertIn('<html lang="es">', es.text)
        self.assertIn('<html lang="en">', en.text)
        self.assertIn("Protegé tus ideas", es.text)
        self.assertIn("Protect your ideas. Prove when they existed.", en.text)
        self.assertNotIn("Protegé tus ideas", en.text)
        self.assertIn('<a href="/" class="hover:text-brand-cyan transition-colors">EN</a>', es.text)  # the selector goes to the main page, in English
        self.assertIn('<a href="/es" class="hover:text-brand-cyan transition-colors">ES</a>', en.text)
        self.assertEqual(c.get("/en").status_code, 404)  # English is the main page, there is no /en
        self.assertIn('name="lang" value="es"', es.text)
        for page in (es.text, en.text):
            self.assertIn('action="/solicitar-registro"', page)
            self.assertNotIn("OpenTimestamps", page)
        self.assertIn('name="lang" value="en"', en.text)

    def test_registration_from_the_english_page_answers_in_english(self):
        c = self.make_client()
        ok = c.post("/solicitar-registro", data={"email": "ana@example.com"})  # no language: English, like the main page
        self.assertEqual(ok.status_code, 200)
        self.assertIn("We sent an email to ana@example.com", ok.text)
        bad = c.post("/solicitar-registro", data={"email": "sin-arroba", "lang": "en"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("The email is not valid.", bad.text)
        self.assertIn("Protect your ideas", bad.text)

    # Flujo completo ----------------------------------------------------------------------

    def test_full_flow_request_registration_email_link_form_evidence_and_worker(self):
        c = self.make_client()
        r = self.register()
        self.assertEqual(r.status_code, 200)
        self.assertIn("Te enviamos un correo a ana@example.com", r.text)
        self.assertEqual(self.count("evidence_requests"), 0)  # requesting the registration creates no request or evidence
        token = self.emailed_link()
        self.assertEqual(len(self.mailer.sent), 1)
        self.assertEqual(self.mailer.sent[0]["To"], "ana@example.com")

        page = c.get(f"/formulario?t={token}")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(self.count("evidences"), 0)  # opening the link confirms the email but generates nothing
        with self.engine.connect() as conn:
            self.assertIsNotNone(conn.execute(text("SELECT confirmed_at FROM email_verifications")).scalar())

        done = c.post("/solicitudes", data=dict(FORM, t=token), files=PDF, follow_redirects=False)
        self.assertEqual(done.status_code, 303, done.text)
        location = done.headers["location"]
        self.assertEqual(self.count("evidences"), 1)  # submitting the form generates the evidence right away

        req = self.svc.requests.list_requests()[0]
        self.assertEqual(req.status, "PROCESSED")
        evidence = self.svc.evidence.get_evidence(req.evidence_id)
        self.assertEqual(evidence.status, EvidenceState.HASHED)
        self.assertEqual(evidence.declaration_version, "1.0")
        user = self.svc.users.get_by_email("ana@example.com")
        self.assertTrue(user.email_verified)
        self.assertEqual(user.country, "AR")
        events = {e.event_type: e for e in self.svc.audit.get_events_for_evidence(evidence.id)}
        self.assertEqual(events["PAYMENT_CONFIRMED"].metadata["method"], "bypass")
        self.assertEqual(events["PAYMENT_CONFIRMED"].metadata["reference"], f"request:{req.id}")
        self.assertFalse(os.path.exists(os.path.join(self.svc.requests.staging_root, req.id)))

        status = c.get(location)
        self.assertIn(req.evidence_id, status.text)
        self.assertIn("preparando el sello", status.text)

        self.svc.worker.run_cycle()
        self.assertEqual(self.svc.evidence.get_evidence(evidence.id).status, EvidenceState.PENDING_BITCOIN)
        self.assertIn("confirmación de Bitcoin", c.get(location).text)

    def test_the_email_comes_from_the_link_not_from_the_form(self):
        self.make_client()
        r = self.post_request({"email": "otra@example.com", "t": link_token(self.svc, "ana@example.com")})
        self.assertEqual(r.status_code, 303, r.text)
        self.assertEqual(self.svc.requests.list_requests()[0].email, "ana@example.com")

    def test_the_link_works_until_the_form_is_sent_and_only_once(self):
        c = self.make_client()
        token = link_token(self.svc)
        for _ in range(2):
            self.assertEqual(c.get(f"/formulario?t={token}").status_code, 200)  # it can be opened as many times as needed
        bad = self.post_request({"t": token, "title": ""})
        self.assertEqual(bad.status_code, 400)  # a rejected submission does not use up the link
        self.assertEqual(self.post_request({"t": token}).status_code, 303)
        self.assertEqual(self.post_request({"t": token}).status_code, 404)
        self.assertEqual(c.get(f"/formulario?t={token}").status_code, 404)
        self.assertEqual(self.count("evidences"), 1)

    def test_text_record_without_files(self):
        self.make_client()
        r = self.post_request({"text_content": "Una idea escrita directamente", "text_title": "Idea"}, files=[])
        self.assertEqual(r.status_code, 303, r.text)
        req = self.svc.requests.list_requests()[0]
        files = self.svc.evidence.get_files(req.evidence_id)
        self.assertEqual([f.mime for f in files], ["text/markdown"])

    # Registration and access by private link ---------------------------------------------------

    def test_registration_rejects_an_invalid_email_and_sends_nothing(self):
        self.make_client()
        for bad in ("sin-arroba", "", "a@b"):
            self.assertEqual(self.register(bad).status_code, 400, bad)
        deliver_notifications(self.svc)
        self.assertEqual(len(self.mailer.sent), 0)
        self.assertEqual(self.count("email_verifications"), 0)

    def test_status_page_requires_the_private_token(self):
        c = self.make_client()
        location = self.submit_ok()
        rid = location.split("/")[2].split("?")[0]
        self.assertEqual(c.get(location).status_code, 200)
        self.assertEqual(c.get(f"/solicitudes/{rid}").status_code, 404)
        self.assertEqual(c.get(f"/solicitudes/{rid}?t=incorrecto").status_code, 404)
        self.assertEqual(c.get("/solicitudes/no-es-un-uuid?t=x").status_code, 404)

    def test_the_form_cannot_be_opened_or_sent_without_a_valid_link(self):
        c = self.make_client()
        self.assertEqual(c.get("/formulario").status_code, 404)
        self.assertEqual(c.get("/formulario?t=nada").status_code, 404)
        self.assertEqual(self.post_request({"t": "nada"}).status_code, 404)
        self.assertEqual(self.post_request({"t": ""}).status_code, 404)
        self.assertEqual(self.count("evidence_requests"), 0)

    def test_tokens_are_stored_hashed(self):
        self.make_client()
        self.register()
        token = self.emailed_link()
        with self.engine.connect() as conn:
            stored = conn.execute(text("SELECT token_hash FROM email_verifications")).scalar()
        self.assertNotEqual(token, stored)
        self.assertEqual(len(stored), 64)

    def test_the_link_expires_after_24_hours(self):
        c = self.make_client()
        token = link_token(self.svc)
        with self.engine.connect() as conn:
            hours = conn.execute(text("SELECT extract(epoch FROM (expires_at - created_at)) / 3600 FROM email_verifications")).scalar()
        self.assertAlmostEqual(float(hours), 24, places=1)
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE email_verifications SET expires_at = now() - interval '1 hour'"))
        self.assertEqual(c.get(f"/formulario?t={token}").status_code, 404)
        self.assertEqual(self.post_request({"t": token}).status_code, 404)
        self.assertEqual(self.count("evidences"), 0)

    def test_a_new_link_invalidates_the_previous_unused_link_of_the_same_email(self):
        c = self.make_client()
        first = link_token(self.svc, "ana@example.com")
        other_email = link_token(self.svc, "otra@example.com")
        second = link_token(self.svc, "ana@example.com")
        self.assertEqual(c.get(f"/formulario?t={first}").status_code, 404)
        self.assertEqual(self.post_request({"t": first}).status_code, 404)
        self.assertEqual(c.get(f"/formulario?t={second}").status_code, 200)
        self.assertEqual(c.get(f"/formulario?t={other_email}").status_code, 200)  # another email: untouched
        self.assertEqual(self.post_request({"t": second}).status_code, 303)
        self.assertEqual(self.count("evidences"), 1)

    def test_requesting_the_registration_twice_leaves_only_the_last_link_alive(self):
        c = self.make_client()
        self.register()
        first = self.emailed_link()
        self.register()
        second = self.emailed_link()
        self.assertNotEqual(first, second)
        self.assertEqual(c.get(f"/formulario?t={first}").status_code, 404)
        self.assertEqual(c.get(f"/formulario?t={second}").status_code, 200)

    def test_a_used_link_does_not_come_back_when_a_new_one_is_requested(self):
        c = self.make_client()
        used = link_token(self.svc)
        self.assertEqual(self.post_request({"t": used}).status_code, 303)
        link_token(self.svc)
        self.assertEqual(c.get(f"/formulario?t={used}").status_code, 404)
        self.assertEqual(self.post_request({"t": used}).status_code, 404)
        self.assertEqual(self.count("evidences"), 1)

    def test_one_link_registers_once_even_with_two_simultaneous_submissions(self):
        self.make_client()
        token = link_token(self.svc)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = sorted(f.result().status_code for f in [pool.submit(self.post_request, {"t": token}) for _ in range(2)])
        self.assertEqual(codes, [303, 404])
        self.assertEqual(self.count("evidence_requests"), 1)

    def test_the_link_is_returned_if_the_request_could_not_be_saved(self):
        c = self.make_client()
        token = link_token(self.svc)
        real_create = self.svc.requests.create

        def broken(*args, **kwargs):
            raise RuntimeError("falla simulada al guardar")
        self.svc.requests.create = broken
        with self.assertRaises(RuntimeError):
            self.post_request({"t": token})
        self.svc.requests.create = real_create
        self.assertEqual(self.count("evidence_requests"), 0)
        self.assertEqual(c.get(f"/formulario?t={token}").status_code, 200)  # the link was not burned
        self.assertEqual(self.post_request({"t": token}).status_code, 303)
        self.assertEqual(self.count("evidences"), 1)

    def test_the_link_stays_spent_when_the_request_was_saved_but_the_pipeline_failed(self):
        c = self.make_client()
        token = link_token(self.svc)

        def boom(*args, **kwargs):
            raise RuntimeError("falla simulada del pipeline")
        self.svc.pipeline.execute_creation_flow = boom
        self.assertEqual(self.post_request({"t": token}).status_code, 303)
        self.assertEqual(self.svc.requests.list_requests()[0].status, "FAILED")
        self.assertEqual(c.get(f"/formulario?t={token}").status_code, 404)

    # Validation and limits -----------------------------------------------------------------

    def test_invalid_submissions_create_nothing(self):
        self.make_client()
        cases = {
            "email inválido": ({"t": link_token(self.svc, "sin-arroba")}, None),
            "sin declaración": ({"declaration_accepted": ""}, None),
            "país inválido": ({"country": "ARG"}, None),
            "país que no existe": ({"country": "ZZ"}, None),
            "categoría inventada": ({"category": "hack"}, None),
            "extensión no permitida": ({}, [("files", ("virus.exe", b"MZ", "application/octet-stream"))]),
            "sin contenido": ({}, []),
            "versión de declaración inexistente": ({"declaration_version": "9.9"}, None),
            "archivo vacío": ({}, [("files", ("vacio.pdf", b"", "application/pdf"))]),
        }
        for label, (data, files) in cases.items():
            with self.subTest(label):
                r = self.post_request(data, files)
                self.assertEqual(r.status_code, 400, label)
        self.assertEqual(self.count("evidence_requests"), 0)
        self.assertEqual(len(self.mailer.sent), 0)

    def test_oversized_file_is_rejected_and_staging_cleaned(self):
        self.make_client(max_file_mb=1)
        r = self.post_request(files=[("files", ("grande.pdf", b"x" * (1024 * 1024 + 1), "application/pdf"))])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.count("evidence_requests"), 0)
        staging = self.svc.requests.staging_root
        self.assertEqual(os.listdir(staging) if os.path.exists(staging) else [], [])

    def test_too_many_files_are_rejected(self):
        self.make_client(max_files_per_request=2)
        files = [("files", (f"a{i}.pdf", b"x", "application/pdf")) for i in range(3)]
        self.assertEqual(self.post_request(files=files).status_code, 400)

    def test_registrations_are_rate_limited(self):
        self.make_client(submit_rate_per_hour=2)
        self.assertEqual(self.register("a@example.com").status_code, 200)
        self.assertEqual(self.register("b@example.com").status_code, 200)
        self.assertEqual(self.register("c@example.com").status_code, 429)
        self.assertEqual(self.count("email_verifications"), 2)

    def test_rate_limit_uses_the_visitor_ip_behind_a_trusted_proxy(self):
        self.make_client(submit_rate_per_hour=1, proxy_ip_header="CF-Connecting-IP", trusted_proxy_ips="testclient")
        first = self.register("a@example.com", headers={"CF-Connecting-IP": "203.0.113.1"})
        other = self.register("b@example.com", headers={"CF-Connecting-IP": "203.0.113.2"})
        again = self.register("c@example.com", headers={"CF-Connecting-IP": "203.0.113.1"})
        self.assertNotEqual(first.status_code, 429)
        self.assertNotEqual(other.status_code, 429)
        self.assertEqual(again.status_code, 429)

    def test_the_proxy_header_is_ignored_when_the_connection_is_not_trusted(self):
        self.make_client(submit_rate_per_hour=1, proxy_ip_header="CF-Connecting-IP", trusted_proxy_ips="198.51.100.2")
        first = self.register("a@example.com", headers={"CF-Connecting-IP": "203.0.113.1"})
        spoofed = self.register("b@example.com", headers={"CF-Connecting-IP": "203.0.113.9"})
        self.assertNotEqual(first.status_code, 429)
        self.assertEqual(spoofed.status_code, 429)

    # Fallas ---------------------------------------------------------------------------------

    def test_pipeline_failure_marks_request_failed_for_the_admin(self):
        c = self.make_client()

        def boom(*args, **kwargs):
            raise RuntimeError("falla simulada del pipeline")
        self.svc.pipeline.execute_creation_flow = boom
        r = self.post_request()
        self.assertEqual(r.status_code, 303)  # the request was recorded, the client sees their status page
        req = self.svc.requests.list_requests()[0]
        self.assertEqual(req.status, "FAILED")
        self.assertTrue(req.error)
        self.assertEqual([x.id for x in self.svc.requests.list_requests(status="FAILED")], [req.id])
        self.assertIn("No pudimos generar la evidencia", c.get(r.headers["location"]).text)

SPANISH_WORDS = ("Completá", "Elegí", "Declaro", "Archivos", "Enviar solicitud", "País", "Título", "Descripción", "Correo confirmado",
                 "Estado de tu solicitud", "Enlace no válido", "Volver al inicio", "Evidencia digital verificable")


class TestLanguage(PostgresTestCase):
    """The client sees their whole journey in the language they requested the registration in. Same setup as TestPortal."""

    make_client = TestPortal.make_client
    post_request = TestPortal.post_request
    register = TestPortal.register

    def test_the_catalog_has_every_text_in_both_languages_with_the_same_placeholders(self):
        from string import Formatter
        from src.core.i18n import MESSAGES

        def fields(text):
            return {f for _, f, _, _ in Formatter().parse(text) if f}
        for key, versions in MESSAGES.items():
            self.assertEqual(set(versions), {"es", "en"}, key)
            self.assertTrue(versions["es"].strip() and versions["en"].strip(), key)
            self.assertEqual(fields(versions["es"]), fields(versions["en"]), key)

    def test_the_form_opened_from_an_english_link_is_entirely_in_english(self):
        c = self.make_client()
        r = c.get(f"/formulario?t={link_token(self.svc, lang='en')}")
        self.assertEqual(r.status_code, 200)
        self.assertIn('<html lang="en">', r.text)
        for expected in ("Complete your request", "Email confirmed", "Choose a country", "Submit request", "Script", "Spain",
                         "I declare that I am submitting this content", "version 1.0-en", "Structured AI operating systems", "Contact"):
            self.assertIn(expected, r.text)
        for spanish in SPANISH_WORDS + ("España", "Guión", "Declaro que presento"):
            self.assertNotIn(spanish, r.text)

    def test_the_form_opened_from_a_spanish_link_is_entirely_in_spanish(self):
        c = self.make_client()
        r = c.get(f"/formulario?t={link_token(self.svc, lang='es')}")
        self.assertIn('<html lang="es">', r.text)
        for expected in ("Completá tu solicitud", "Correo confirmado", "Elegí un país", "Enviar solicitud", "Guión", "España",
                         "Declaro que presento este contenido", "versión 1.0"):
            self.assertIn(expected, r.text)
        for english in ("Complete your request", "Email confirmed", "Submit request", "I declare that", "Spain"):
            self.assertNotIn(english, r.text)

    def test_countries_and_categories_are_listed_alphabetically_in_each_language(self):
        from src.core.countries import COUNTRIES, COUNTRIES_EN
        self.assertEqual({c for c, _ in COUNTRIES_EN}, {c for c, _ in COUNTRIES})
        self.assertEqual(len(COUNTRIES_EN), len(COUNTRIES))
        import unicodedata
        key = lambda n: unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode().lower()
        names = [n for _, n in COUNTRIES_EN]
        self.assertEqual(names, sorted(names, key=key))

    def test_validation_errors_are_shown_in_the_language_of_the_link(self):
        self.make_client()
        en = self.post_request({"t": link_token(self.svc, lang="en"), "title": "", "declaration_version": "1.0-en"})
        self.assertEqual(en.status_code, 400)
        self.assertIn("The title is required.", en.text)
        self.assertNotIn("obligatorio", en.text)
        es = self.post_request({"t": link_token(self.svc, lang="es"), "title": ""})
        self.assertEqual(es.status_code, 400)
        self.assertIn("El título es obligatorio.", es.text)
        self.assertNotIn("is required", es.text)

    def test_file_errors_are_shown_in_the_language_of_the_link(self):
        self.make_client()
        bad = [("files", ("virus.exe", b"MZ", "application/octet-stream"))]
        en = self.post_request({"t": link_token(self.svc, lang="en"), "declaration_version": "1.0-en"}, files=bad)
        self.assertIn("File type not accepted: &#39;virus.exe&#39;.", en.text)
        es = self.post_request({"t": link_token(self.svc, lang="es")}, files=bad)
        self.assertIn("Tipo de archivo no aceptado: &#39;virus.exe&#39;.", es.text)
        empty = [("files", ("vacio.txt", b"", "text/plain"))]
        self.assertIn("The file &#39;vacio.txt&#39; is empty.", self.post_request({"t": link_token(self.svc, lang="en"), "declaration_version": "1.0-en"}, files=empty).text)

    def test_oversized_file_error_is_shown_in_the_language_of_the_link(self):
        self.make_client(max_file_mb=1)
        big = [("files", ("grande.txt", b"x" * (1024 * 1024 + 1), "text/plain"))]
        en = self.post_request({"t": link_token(self.svc, lang="en"), "declaration_version": "1.0-en"}, files=big)
        self.assertIn("exceeds the 1 MB limit", en.text)

    def test_the_english_declaration_is_the_one_recorded_for_an_english_client(self):
        self.make_client()
        r = self.post_request({"t": link_token(self.svc, lang="en"), "declaration_version": "1.0-en"})
        self.assertEqual(r.status_code, 303, r.text)
        evidence = self.svc.evidence.get_evidence(self.svc.requests.list_requests()[0].evidence_id)
        self.assertEqual(evidence.declaration_version, "1.0-en")
        self.assertEqual(self.svc.legal.latest("declaration", "en")["version"], "1.0-en")
        self.assertEqual(self.svc.legal.latest("declaration", "es")["version"], "1.0")

    def test_the_status_page_is_in_the_language_of_the_request(self):
        c = self.make_client()
        en = c.get(self.post_request({"t": link_token(self.svc, "ana@example.com", "en"), "declaration_version": "1.0-en"}).headers["location"])
        self.assertIn("Your request status", en.text)
        self.assertIn("Your evidence was generated.", en.text)
        self.assertIn("can take between 1 and 12 hours, depending on network congestion", en.text)
        self.assertIn("Prima is free. Donations keep it running.", en.text)  # the free block is also on the status page
        self.assertLess(en.text.index("Request details"), en.text.index('id="donar"'))
        self.assertIn("we will send your certificate to ana@example.com", en.text)
        for spanish in SPANISH_WORDS + ("Presentada por", "Huella digital"):
            self.assertNotIn(spanish, en.text)
        es = c.get(self.post_request({"t": link_token(self.svc, "beto@example.com", "es"), "email": "beto@example.com"}).headers["location"])
        self.assertIn("Estado de tu solicitud", es.text)
        self.assertIn("Presentada por", es.text)
        self.assertIn("puede tardar entre 1 y 12 horas, según la congestión de la red", es.text)
        self.assertIn("te enviamos el certificado a beto@example.com", es.text)
        self.assertNotIn("Your request status", es.text)

    def test_the_status_page_tells_the_state_of_the_evidence_in_the_language_of_the_request(self):
        c = self.make_client()
        location = self.post_request({"t": link_token(self.svc, lang="en"), "declaration_version": "1.0-en"}).headers["location"]
        self.svc.worker.run_cycle()
        self.assertIn("Waiting for Bitcoin confirmation", c.get(location).text)

    def test_error_pages_use_the_language_of_the_link_or_english_when_it_is_unknown(self):
        c = self.make_client()
        unknown = c.get("/formulario?t=nada")
        self.assertEqual(unknown.status_code, 404)
        self.assertIn("Invalid link", unknown.text)
        self.assertIn('<a href="/">Back to home</a>', unknown.text)
        spanish = link_token(self.svc, lang="es")
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE email_verifications SET expires_at = now() - interval '1 hour'"))
        expired = c.get(f"/formulario?t={spanish}")
        self.assertEqual(expired.status_code, 404)
        self.assertIn("Enlace no válido", expired.text)
        self.assertIn('<a href="/es">Volver al inicio</a>', expired.text)
        missing = c.get("/solicitudes/no-es-un-uuid?t=x")
        self.assertIn("We could not find that request", missing.text)

    def test_the_free_service_block_comes_after_the_email_form_with_the_button_under_the_text_in_both_languages(self):
        c = self.make_client()
        stripe = "https://buy.stripe.com/aFa8wR3tgfrbeWcfNK0x20a"
        en = ("Prima is free. Donations keep it running.", "If you cannot donate, register your ideas anyway.",
              "Donating is optional and is not a payment for registering.", "Support Prima")
        es = ("Prima es gratis. Las donaciones la mantienen funcionando.", "Si no podés donar, registrá tus ideas igual.",
              "Donar es opcional y no es un pago por registrar.", "Apoyar a Prima")
        for path, texts, note, other in (("/", en, "Donating is optional", es), ("/es", es, "Donar es opcional", en)):
            html = c.get(path).text
            for expected in texts:
                self.assertIn(expected, html, path)
            for absent in other:
                self.assertNotIn(absent, html, path)
            self.assertEqual(html.count(stripe), 1, path)
            self.assertIn(f'href="{stripe}" target="_blank" rel="noopener noreferrer"', html)
            self.assertLess(html.index('id="solicitud"'), html.index('id="donar"'), path)  # after the email box
            self.assertLess(html.index(note), html.index(stripe), path)  # the button goes below the text
            self.assertEqual(html.count('<button type="submit">'), 1, path)  # the donation is a link, it does not compete with the registration

    def test_the_form_page_has_the_fragola_header_the_free_service_block_and_the_fragola_footer_in_both_languages(self):
        c = self.make_client()
        for lang, header, block, footer in (("en", "Contact", "Prima is free. Donations keep it running.", "Structured AI operating systems"),
                                            ("es", "Contacto", "Prima es gratis. Las donaciones la mantienen funcionando.", "Sistemas operativos de IA")):
            html = c.get(f"/formulario?t={link_token(self.svc, f'{lang}@example.com', lang)}").text
            positions = [html.index("<nav"), html.index("</form>"), html.index(block), html.index(footer)]
            self.assertEqual(positions, sorted(positions), lang)  # header, formulario, bloque gratuito, footer
            self.assertIn(header, html)

    def test_the_box_below_the_home_verifies_the_email_and_shows_the_time_to_fill_the_form(self):
        c = self.make_client()
        en, es = c.get("/").text, c.get("/es").text
        self.assertIn("Verify email and access the form", en)
        self.assertIn("Completing the form takes about 3 minutes.", en)
        self.assertNotIn("Request registration", en)
        self.assertNotIn("Request the registration", en)
        self.assertIn("Verificar email y acceder al formulario", es)
        self.assertIn("Completar el formulario lleva unos 3 minutos.", es)
        self.assertNotIn("Solicitá el registro", es)
        self.assertIn('class="time-note"><svg', en)  # the little clock

    def test_registration_messages_come_from_the_catalog_in_each_language(self):
        self.make_client(submit_rate_per_hour=2)
        self.assertIn("The email is not valid.", self.register("sin-arroba", lang="en").text)
        self.register("a@example.com", lang="en")
        limited_en = self.register("b@example.com", lang="en")
        self.assertEqual(limited_en.status_code, 429)
        self.assertIn("Too many requests from this connection. Try again in an hour.", limited_en.text)
        limited_es = self.register("c@example.com", lang="es")
        self.assertIn("Demasiados pedidos desde esta conexión. Probá de nuevo en una hora.", limited_es.text)


if __name__ == "__main__":
    unittest.main()
