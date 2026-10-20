import hashlib
import json
import re
import time
import unittest
from pathlib import Path
from db_helper import deliver_notifications
from test_mcp_entry import EMAIL, TestMcpEntry
from test_no_storage import stored_files
from fragola_mcp.prima.core import (METADATA_FILE, Config, PrimaBusy, PrimaClient, PrimaError, check_limits, confirm_connection, connect,
                                    extract_token, load_index, load_key, load_receipt, process_pending, project_dir,
                                    refresh_status, register_file, show_declaration)

CONTENT = b"%PDF-1.4 propuesta para el cliente"

class TestPrimaMcp(TestMcpEntry):
    """The MCP program against the real server entry (the HTTP client is FastAPI's test client)."""

    def run(self, result=None):
        if self._testMethodName.startswith("test_prima_"):
            return unittest.TestCase.run(self, result)
        return None

    def setup_mcp(self, connected=True, **server):
        self.setup_entry(**server)
        self.work = Path(self.tmp) / "pc"
        self.work.mkdir()
        self.cfg = Config(api_url="", folder=self.work / "Certificaciones", email=EMAIL, name="Ana Prueba", country="AR",
                          config_dir=self.work / "config", key=self.key if connected else "", lang="es", max_video_mb=1)
        self.client_ = PrimaClient(self.cfg, session=self.client)

    def make_file(self, name="propuesta.pdf", data=CONTENT):
        path = self.work / name
        path.write_bytes(data)
        return str(path)

    def register(self, path=None, show=True, **kw):
        if show:
            show_declaration(self.cfg, self.client_)  # the real flow: the declaration is requested and shown first
        kw.setdefault("declaration_accepted", True)
        return register_file(self.cfg, self.client_, "Nike 2026", path or self.make_file(), **kw)

    # Declaration -------------------------------------------------------------------------

    def test_prima_the_declaration_must_be_requested_before_registering(self):
        self.setup_mcp()
        with self.assertRaises(PrimaError) as ctx:
            self.register(show=False)
        self.assertIn("prima_ver_declaracion", str(ctx.exception))
        self.assertEqual(self.count("evidence_requests"), 0)
        shown = show_declaration(self.cfg, self.client_)
        self.assertEqual(shown["version"], "1.0")
        self.assertTrue(shown["text"])
        self.assertIn("full text", shown["instruction"])
        self.assertNotIn("receipt", shown)  # the receipt stays on the computer, it is shown to nobody
        self.assertEqual(load_receipt(self.cfg)["version"], "1.0")
        self.assertEqual(self.register(show=False)["status"], "PROCESSED")

    def test_prima_an_old_receipt_makes_the_server_ask_for_the_declaration_again(self):
        self.setup_mcp(mcp_declaration_receipt_minutes=0)
        show_declaration(self.cfg, self.client_)
        time.sleep(1.2)
        with self.assertRaises(PrimaError) as ctx:
            self.register(show=False)
        self.assertIn("declaración", str(ctx.exception))
        self.assertEqual(self.count("evidence_requests"), 0)

    # Connection ---------------------------------------------------------------------------

    def test_prima_connecting_confirms_the_email_once_and_saves_the_key_on_this_pc(self):
        self.setup_mcp(connected=False)
        with self.assertRaises(PrimaError) as ctx:
            self.register()
        self.assertIn("prima_conectar", str(ctx.exception))
        self.assertEqual(connect(self.cfg, self.client_)["sent_to"], EMAIL)
        deliver_notifications(self.svc)
        link = re.search(r"http\S*/formulario\?t=[\w-]+", self.mailer.sent[-1].get_content()).group(0)
        done = confirm_connection(self.cfg, self.client_, link)  # the full link is pasted
        self.assertEqual(done["email"], EMAIL)
        self.assertNotIn("prima_", str(done))  # the key is shown to nobody
        self.assertTrue(self.cfg.key.startswith("prima_"))
        self.assertEqual(load_key(self.cfg), self.cfg.key)
        self.assertEqual(self.register()["status"], "PROCESSED")
        self.assertEqual(self.rows("SELECT source FROM evidence_requests")[0].source, "mcp")

    def test_prima_not_connected_the_first_thing_it_asks_for_is_to_connect(self):
        self.setup_mcp(connected=False)
        with self.assertRaises(PrimaError) as ctx:
            register_file(self.cfg, self.client_, "Nike 2026", self.make_file(), declaration_accepted=True)  # no key and no declaration
        self.assertIn("prima_conectar", str(ctx.exception))
        self.assertNotIn("prima_ver_declaracion", str(ctx.exception))
        self.assertEqual(self.count("evidence_requests"), 0)

    def test_prima_a_used_or_wrong_link_is_a_readable_error(self):
        self.setup_mcp(connected=False)
        with self.assertRaises(PrimaError):
            confirm_connection(self.cfg, self.client_, "https://x/formulario?t=inventado")
        with self.assertRaises(PrimaError):
            extract_token("   ")
        self.assertEqual(extract_token("abc123"), "abc123")
        self.assertEqual(extract_token("https://certified.fragola.agency/formulario?t=ABC-def_1"), "ABC-def_1")

    # Registro ---------------------------------------------------------------------------

    def test_prima_registers_by_fingerprint_keeps_the_original_and_the_server_keeps_nothing(self):
        self.setup_mcp()
        entry = self.register(category="propuesta_comercial", title="Propuesta Nike")
        pdir = project_dir(self.cfg, "Nike 2026")
        self.assertEqual((pdir / "propuesta.pdf").read_bytes(), CONTENT)
        self.assertEqual(entry["sha256"], hashlib.sha256(CONTENT).hexdigest())
        self.assertEqual(entry["status"], "PROCESSED")
        saved = load_index(pdir)["entries"]
        self.assertEqual([(e["filename"], e["sha256"], e["request_id"]) for e in saved],
                         [("propuesta.pdf", entry["sha256"], entry["request_id"])])
        self.assertEqual(self.rows("SELECT source, project FROM evidence_requests")[0][:2], ("mcp", "Nike 2026"))
        self.assertEqual(self.rows("SELECT storage_reference FROM evidence_files")[0].storage_reference, "")
        self.assertEqual(stored_files(self.settings.storage_root), [])

    def test_prima_the_same_file_is_not_registered_twice(self):
        self.setup_mcp()
        first = self.register()
        again = self.register()
        self.assertTrue(again["already_registered"])
        self.assertEqual(again["request_id"], first["request_id"])
        self.assertEqual(self.count("evidence_requests"), 1)

    def test_prima_without_accepting_the_declaration_nothing_is_registered(self):
        self.setup_mcp()
        with self.assertRaises(PrimaError) as ctx:
            self.register(declaration_accepted=False)
        self.assertIn("declaration", str(ctx.exception))
        self.assertEqual(self.count("evidence_requests"), 0)

    def test_prima_a_revoked_key_is_a_readable_error(self):
        self.setup_mcp()
        self.svc.mcp_keys.create_key(EMAIL)  # another new key revokes the one this computer has
        with self.assertRaises(PrimaError):
            self.register()

    def test_prima_a_busy_server_is_a_notice_not_an_error_type(self):
        self.setup_mcp()
        outer = self

        class Busy:
            status_code = 503
            def json(self):
                return {"error": {"code": "busy", "message": "Esperá unos minutos."}}

        class Session:
            def post(self, *a, **k):
                return Busy()
            def get(self, *a, **k):
                return outer.client.get(*a, **k)
        show_declaration(self.cfg, self.client_)
        client = PrimaClient(self.cfg, session=Session())
        with self.assertRaises(PrimaBusy):
            register_file(self.cfg, client, "Nike 2026", self.make_file(), declaration_accepted=True)

    # Limits: size only, no duration or height ---------------------------------------------

    def test_prima_a_video_over_the_weight_limit_says_how_to_fix_it_and_registers_nothing(self):
        self.setup_mcp()
        path = self.make_file("clip.mp4", b"\x00" * (1024 * 1024 + 1))
        with self.assertRaises(PrimaError) as ctx:
            self.register(path)
        self.assertIn("Limit exceeded", str(ctx.exception))
        self.assertIn("prima_reducir_video", str(ctx.exception))
        self.assertIn("480p", str(ctx.exception))
        self.assertEqual(self.count("evidence_requests"), 0)

    def test_prima_a_video_within_the_weight_limit_is_accepted_whatever_its_duration_or_height(self):
        self.setup_mcp()
        self.assertEqual(self.register(self.make_file("clip.mp4", b"\x00" * 5000))["status"], "PROCESSED")

    def test_prima_an_empty_or_unaccepted_file_is_refused_locally(self):
        self.setup_mcp()
        with self.assertRaises(PrimaError):
            check_limits(self.cfg, Path(self.make_file("vacio.pdf", b"")))
        with self.assertRaises(PrimaError):
            check_limits(self.cfg, Path(self.make_file("virus.exe", b"MZ")))

    # Queue and status --------------------------------------------------------------------------

    def test_prima_the_pending_queue_registers_new_files_and_skips_certificates_and_the_local_register(self):
        self.setup_mcp()
        pdir = project_dir(self.cfg, "Nike 2026")
        (pdir / "certificados").mkdir(parents=True)
        (pdir / "certificados" / "certificate.pdf").write_bytes(b"pdf del certificado")
        (pdir / "a.pdf").write_bytes(b"%PDF uno")
        (pdir / "sub").mkdir()
        (pdir / "sub" / "b.txt").write_bytes(b"dos")
        (pdir / "malo.exe").write_bytes(b"MZ")
        show_declaration(self.cfg, self.client_)
        result = process_pending(self.cfg, self.client_, declaration_accepted=True)
        self.assertEqual(sorted(r["filename"] for r in result["registered"]), ["a.pdf", "b.txt"])
        self.assertEqual([s["filename"] for s in result["skipped"]], ["malo.exe"])
        self.assertEqual(self.count("evidence_requests"), 2)
        again = process_pending(self.cfg, self.client_, declaration_accepted=True)
        self.assertEqual(again["registered"], [])
        self.assertEqual(self.count("evidence_requests"), 2)

    def test_prima_the_status_refreshes_the_local_register(self):
        self.setup_mcp()
        self.register()
        rows = refresh_status(self.cfg, self.client_, "Nike 2026")
        self.assertEqual((rows[0]["filename"], rows[0]["status"]), ("propuesta.pdf", "PROCESSED"))
        self.assertTrue(rows[0]["evidence_id"])
        self.assertTrue(load_index(project_dir(self.cfg, "Nike 2026"))["entries"][0]["manifest_hash"])

    def test_prima_the_project_metadata_is_jsonld_without_personal_data_or_keys(self):
        self.setup_mcp()
        entry = self.register()
        refresh_status(self.cfg, self.client_, "Nike 2026")
        path = project_dir(self.cfg, "Nike 2026") / METADATA_FILE
        meta = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((meta["@context"], meta["@type"], meta["name"]), ("https://schema.org", "Dataset", "Nike 2026"))
        part = meta["hasPart"][0]
        self.assertEqual((part["name"], part["sha256"], part["encodingFormat"]), ("propuesta.pdf", entry["sha256"], "application/pdf"))
        self.assertTrue(part["identifier"])
        self.assertIn("manifest_sha256", [p["name"] for p in part["additionalProperty"]])
        raw = path.read_text(encoding="utf-8")
        for secret in (EMAIL, "Ana Prueba", entry["access_token"], entry["request_id"], self.cfg.key):
            self.assertNotIn(secret, raw)
        with self.assertRaises(PrimaError):
            register_file(self.cfg, self.client_, "Nike 2026", str(path), declaration_accepted=True)
        self.assertEqual(process_pending(self.cfg, self.client_, declaration_accepted=True)["registered"], [])

    def test_prima_optional_version_and_orcid_stay_on_the_pc(self):
        self.setup_mcp()
        self.register(description="Propuesta final", version="v2", orcid="https://orcid.org/0000-0002-1825-009x")
        meta = json.loads((project_dir(self.cfg, "Nike 2026") / METADATA_FILE).read_text(encoding="utf-8"))
        part = meta["hasPart"][0]
        self.assertEqual((part["description"], part["version"]), ("Propuesta final", "v2"))
        self.assertEqual(part["creator"]["identifier"], "https://orcid.org/0000-0002-1825-009X")
        row = self.rows("SELECT * FROM evidence_requests")[0]
        self.assertNotIn("0000-0002-1825-009X", repr(row))
        self.assertNotIn("v2", [v for v in row if isinstance(v, str)])
        manifest = self.rows("SELECT manifest_json FROM proofs")[0].manifest_json
        self.assertNotIn("orcid", manifest.lower())
        self.assertNotIn("0000-0002-1825-009X", manifest)

    def test_prima_without_optional_details_nothing_extra_is_saved(self):
        self.setup_mcp()
        self.register(path=self.make_file("b.pdf", b"%PDF otro"))
        part = json.loads((project_dir(self.cfg, "Nike 2026") / METADATA_FILE).read_text(encoding="utf-8"))["hasPart"][0]
        for key in ("description", "version", "creator"):
            self.assertNotIn(key, part)

    def test_prima_an_invalid_orcid_is_refused_before_registering(self):
        self.setup_mcp()
        with self.assertRaises(PrimaError) as ctx:
            self.register(orcid="1234")
        self.assertIn("ORCID", str(ctx.exception))
        self.assertEqual(self.count("evidence_requests"), 0)

    def test_prima_project_names_cannot_escape_the_folder(self):
        self.setup_mcp()
        for bad in ("", "..", "../fuera", "a/b", ".prima", "x:y"):
            with self.assertRaises(PrimaError, msg=bad):
                project_dir(self.cfg, bad)

if __name__ == "__main__":
    unittest.main()
