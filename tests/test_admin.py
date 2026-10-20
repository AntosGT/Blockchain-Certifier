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
from src.core.admin_auth import hash_password, verify_password
from src.core.config import Settings
from src.core.mailer import OutboxMailer

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "contrasena-de-prueba-123"

FORM = {
    "title": "Guion del cortometraje", "category": "guion", "description": "Primera versión",
    "name": "Ana Prueba", "email": "ana@example.com", "country": "AR", "document_id": "30111222",
    "declaration_version": "1.0", "declaration_accepted": "1",
}

def fake_submit(sha256_hex):
    return b"FAKE-OTS-" + sha256_hex.encode()

ADMIN_PAGES = ["/admin", "/admin/solicitudes", "/admin/evidencias"]

class TestAdminDashboard(PostgresTestCase):

    def make_client(self, **overrides):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        cfg = dict(_env_file=None, storage_root=os.path.join(self.tmp, "storage"), outbox_dir=os.path.join(self.tmp, "outbox"),
                   public_base_url="http://testserver")
        cfg.update(overrides)
        self.settings = Settings(**cfg)
        self.mailer = OutboxMailer(self.settings.outbox_dir)
        self.svc = build_services(self.settings, engine=self.engine, mailer=self.mailer, submit_fn=fake_submit)
        self.svc.users.create_admin(ADMIN_EMAIL, "Admin Prueba", ADMIN_PASSWORD)
        self.client = TestClient(create_app(self.settings, self.svc))
        return self.client

    def login(self, email=ADMIN_EMAIL, password=ADMIN_PASSWORD):
        return self.client.post("/admin/login", data={"email": email, "password": password}, follow_redirects=False)

    def submit_and_confirm(self, **data):
        """Submits the form with the emailed link: the request is processed and the evidence generated."""
        form = dict(FORM, **data)
        form["t"] = link_token(self.svc, form["email"])
        files = [("files", ("guion.pdf", b"%PDF-1.4 contenido", "application/pdf"))]
        r = self.client.post("/solicitudes", data=form, files=files, follow_redirects=False)
        self.assertEqual(r.status_code, 303, r.text)
        deliver_notifications(self.svc)

    def waiting_request(self, **data):
        """An old request left waiting for email confirmation (the previous flow): created directly in the database."""
        from uuid import uuid4
        form = dict(FORM, **data)
        form["document_id"] = None
        self.svc.requests.create(str(uuid4()), form, [], 48)

    # Acceso ---------------------------------------------------------------------------------

    def test_every_admin_page_requires_login(self):
        c = self.make_client()
        for path in ADMIN_PAGES + ["/admin/solicitudes/" + "0" * 8 + "-0000-0000-0000-" + "0" * 12, "/admin/evidencias/PRIMA-X"]:
            r = c.get(path, follow_redirects=False)
            self.assertEqual((r.status_code, r.headers["location"]), (303, "/admin/login"), path)

    def test_login_ok_sets_a_hardened_cookie(self):
        c = self.make_client()
        r = self.login()
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/admin"))
        cookie = r.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertEqual(c.get("/admin").status_code, 200)

    def test_wrong_credentials_get_the_same_generic_error(self):
        self.make_client()
        wrong_password = self.login(password="otra-contrasena-123")
        unknown_user = self.login(email="nadie@example.com")
        self.assertEqual((wrong_password.status_code, unknown_user.status_code), (401, 401))
        self.assertEqual(wrong_password.text, unknown_user.text)
        self.assertNotIn("set-cookie", wrong_password.headers)

    def test_a_client_account_cannot_log_in(self):
        c = self.make_client()
        self.submit_and_confirm()  # creates the client user ana@example.com, without a password
        self.assertEqual(self.login(email="ana@example.com", password="cualquiera-123456").status_code, 401)
        self.assertEqual(c.get("/admin", follow_redirects=False).status_code, 303)

    def test_login_is_rate_limited(self):
        self.make_client(login_rate_per_hour=3)
        codes = [self.login(password="mala-contrasena-123").status_code for _ in range(5)]
        self.assertEqual(codes, [401, 401, 401, 429, 429])
        self.assertEqual(self.login().status_code, 429)  # not even the right password gets through while the lock lasts

    def test_tampered_or_foreign_cookie_is_rejected(self):
        c = self.make_client()
        c.cookies.set("prima_admin", "valor-inventado", path="/admin")
        self.assertEqual(c.get("/admin", follow_redirects=False).status_code, 303)

    def test_losing_the_admin_role_ends_the_session(self):
        c = self.make_client()
        self.login()
        self.assertEqual(c.get("/admin").status_code, 200)
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE users SET role = 'user' WHERE email = :e"), {"e": ADMIN_EMAIL})
        self.assertEqual(c.get("/admin", follow_redirects=False).status_code, 303)

    def test_logout_needs_the_csrf_token(self):
        c = self.make_client()
        self.login()
        page = c.get("/admin").text
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        c.post("/admin/logout", data={"csrf": "falso"}, follow_redirects=False)
        self.assertEqual(c.get("/admin").status_code, 200)  # with an invalid CSRF it does not log out
        c.post("/admin/logout", data={"csrf": csrf}, follow_redirects=False)
        self.assertEqual(c.get("/admin", follow_redirects=False).status_code, 303)

    def test_admin_pages_are_not_indexable_or_cacheable(self):
        c = self.make_client()
        self.login()
        r = c.get("/admin")
        self.assertIn("noindex", r.headers["x-robots-tag"])
        self.assertEqual(r.headers["cache-control"], "no-store")

    # Passwords ------------------------------------------------------------------------------

    def test_password_hashing_and_policy(self):
        h = hash_password("una-contrasena-larga-1")
        self.assertTrue(verify_password("una-contrasena-larga-1", h))
        self.assertFalse(verify_password("otra-contrasena-larga", h))
        self.assertNotEqual(h, hash_password("una-contrasena-larga-1"))  # cada hash lleva su propia sal
        self.assertNotIn("una-contrasena", h)
        self.make_client()
        with self.assertRaises(ValueError):
            self.svc.users.create_admin("corta@example.com", "Corta", "corta")

    # Dashboard content ---------------------------------------------------------------------

    def test_dashboard_counts_and_lists_follow_the_data(self):
        c = self.make_client()
        self.login()
        empty = c.get("/admin").text
        self.assertIn("Todavía no hay solicitudes", empty)

        self.submit_and_confirm()
        self.submit_and_confirm(title="Segundo proyecto", email="beto@example.com", name="Beto Prueba")
        # a third one is left waiting for email confirmation
        self.waiting_request(title="Sin confirmar", email="caro@example.com", name="Caro Prueba")

        counts = self.svc.admin_queries.request_counts()
        self.assertEqual((counts["PROCESSED"], counts["PENDING_EMAIL"]), (2, 1))
        self.assertEqual(self.svc.admin_queries.evidence_counts(), {"HASHED": 2})

        page = c.get("/admin").text
        for expected in ("Guion del cortometraje", "Segundo proyecto", "Sin confirmar", "Ana Prueba"):
            self.assertIn(expected, page)

        pending = c.get("/admin/solicitudes?estado=PENDING_EMAIL").text
        self.assertIn("Sin confirmar", pending)
        self.assertNotIn("Segundo proyecto", pending)

    def test_search_filters_and_treats_wildcards_literally(self):
        c = self.make_client()
        self.submit_and_confirm()
        self.submit_and_confirm(title="Segundo proyecto", email="beto@example.com", name="Beto Prueba")
        self.login()
        self.assertIn("Guion del cortometraje", c.get("/admin/solicitudes?q=ana@").text)
        self.assertNotIn("Segundo proyecto", c.get("/admin/solicitudes?q=ana@").text)
        self.assertIn("No hay solicitudes", c.get("/admin/solicitudes?q=%25").text)   # '%' is not a wildcard
        self.assertIn("No hay solicitudes", c.get("/admin/solicitudes?q=_").text)     # '_' tampoco
        self.assertIn("Segundo proyecto", c.get("/admin/evidencias?q=beto").text)
        self.assertNotIn("Guion del cortometraje", c.get("/admin/evidencias?q=beto").text)
        self.assertEqual(c.get("/admin/evidencias?estado=INVENTADO").status_code, 200)  # an invalid filter is ignored

    def test_filter_by_client_in_requests_and_evidences(self):
        c = self.make_client()
        self.submit_and_confirm()
        self.submit_and_confirm(title="Proyecto de Beto", email="beto@example.com", name="Beto Prueba")
        self.submit_and_confirm(title="Otro de Ana")
        self.waiting_request(title="Pendiente de Caro", email="caro@example.com", name="Caro Prueba")
        self.login()

        ana_requests = c.get("/admin/solicitudes?cliente=ana@example.com").text
        self.assertIn("Guion del cortometraje", ana_requests)
        self.assertIn("Otro de Ana", ana_requests)
        self.assertNotIn("Proyecto de Beto", ana_requests)

        ana_evidences = c.get("/admin/evidencias?cliente=ana@example.com").text
        self.assertIn("Otro de Ana", ana_evidences)
        self.assertNotIn("Proyecto de Beto", ana_evidences)

        # whoever has not confirmed the email yet also shows up as a client and can be filtered
        self.assertIn("Pendiente de Caro", c.get("/admin/solicitudes?cliente=caro@example.com").text)
        self.assertNotIn("Guion del cortometraje", c.get("/admin/solicitudes?cliente=caro@example.com").text)

        # the dropdown lists everyone and marks the chosen one; the client name is a shortcut to the filter
        page = c.get("/admin/solicitudes?cliente=beto@example.com").text
        self.assertIn('<option value="beto@example.com" selected>', page)
        self.assertIn("Caro Prueba (caro@example.com)", page)
        self.assertIn("/admin/solicitudes?cliente=beto%40example.com", page)

        # it combines with the other filters, and a client that does not exist gives an empty list
        self.assertNotIn("Otro de Ana", c.get("/admin/evidencias?cliente=ana@example.com&q=guion").text)
        self.assertIn("No hay evidencias", c.get("/admin/evidencias?cliente=nadie@example.com").text)
        self.assertIn("No hay solicitudes", c.get("/admin/solicitudes?cliente=%25").text)

    def test_evidence_detail_shows_hash_files_and_custody_trail(self):
        c = self.make_client()
        self.submit_and_confirm()
        evidence = self.svc.evidence.list_evidences_for_user(self.svc.users.get_by_email("ana@example.com").id)[0]
        self.login()
        page = c.get(f"/admin/evidencias/{evidence.id}").text
        self.assertIn(evidence.manifest_hash, page)
        self.assertIn("guion.pdf", page)
        self.assertIn("Pago confirmado", page)
        self.assertIn("bypass", page)
        self.assertIn("Huella generada", page)
        self.assertIn("Guion del cortometraje", page)

    def test_request_detail_and_cross_links(self):
        c = self.make_client()
        self.submit_and_confirm()
        self.login()
        req = self.svc.requests.list_requests()[0]
        detail = c.get(f"/admin/solicitudes/{req.id}").text
        self.assertIn(f"/admin/evidencias/{req.evidence_id}", detail)
        self.assertIn("guion.pdf", detail)
        self.assertIn("30111222", detail)
        self.assertIn(f"/admin/solicitudes/{req.id}", c.get(f"/admin/evidencias/{req.evidence_id}").text)

    def test_unknown_or_malformed_ids_give_404_not_500(self):
        c = self.make_client()
        self.login()
        self.assertEqual(c.get("/admin/solicitudes/no-es-un-uuid").status_code, 404)
        self.assertEqual(c.get("/admin/solicitudes/00000000-0000-0000-0000-000000000000").status_code, 404)
        self.assertEqual(c.get("/admin/evidencias/NO-EXISTE").status_code, 404)

    def test_user_input_is_escaped_in_the_dashboard(self):
        c = self.make_client()
        self.submit_and_confirm(title="<script>alert(1)</script>", name="<img src=x onerror=alert(2)>")
        self.login()
        for path in ("/admin", "/admin/solicitudes", "/admin/evidencias"):
            page = c.get(path).text
            self.assertNotIn("<script>alert(1)</script>", page, path)
            self.assertNotIn("<img src=x", page, path)

    def test_dashboard_is_read_only_there_is_no_way_to_change_evidence(self):
        c = self.make_client()
        self.submit_and_confirm()
        self.login()
        evidence_id = self.svc.requests.list_requests()[0].evidence_id
        before = self.svc.evidence.get_evidence(evidence_id)
        for method in ("post", "put", "patch", "delete"):
            r = getattr(c, method)(f"/admin/evidencias/{evidence_id}", follow_redirects=False)
            self.assertIn(r.status_code, (404, 405), method)
        self.assertEqual(self.svc.evidence.get_evidence(evidence_id).manifest_hash, before.manifest_hash)

    def test_new_content_types_are_offered_and_accepted(self):
        c = self.make_client()
        form = c.get(f"/formulario?t={link_token(self.svc)}").text
        self.assertIn("Lógica de sistema", form)
        self.assertIn(">Sistema<", form)
        self.submit_and_confirm(category="logica_de_sistema", title="Reglas de cobro")
        self.submit_and_confirm(category="sistema", title="Arquitectura general", email="beto@example.com", name="Beto Prueba")
        self.login()
        self.assertIn("Lógica de sistema", c.get("/admin/solicitudes").text)
        self.assertIn("Arquitectura general", c.get("/admin/evidencias?categoria=sistema").text)
        self.assertNotIn("Reglas de cobro", c.get("/admin/evidencias?categoria=sistema").text)

if __name__ == "__main__":
    unittest.main()
