import os
import unittest
from sqlalchemy import text
from test_api import TestPublicApi
from test_portal import PDF

def stored_files(root):
    """Stored user files: whatever is under storage_root outside the staging folder and the certificates."""
    found = []
    for folder, dirs, names in os.walk(root):
        rel = os.path.relpath(folder, root)
        if rel.split(os.sep)[0] in ("_requests", "certificates"):
            continue
        found += [os.path.join(rel, n) for n in names]
    return found

class TestNoStorage(TestPublicApi):
    """The server does not keep user files, rejects large videos and warns when it is busy."""

    def run(self, result=None):
        if self._testMethodName.startswith("test_nostore_"):
            return unittest.TestCase.run(self, result)
        return None

    def file_rows(self):
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT filename, sha256, size, storage_reference FROM evidence_files")).all()

    def test_nostore_the_file_is_hashed_but_not_saved(self):
        self.make_client()
        r = self.post_api({"declaration_version": "1.0"})
        self.assertEqual(r.status_code, 200, r.text)
        rows = self.file_rows()
        self.assertEqual(len(rows), 1)
        import hashlib
        self.assertEqual(rows[0].sha256, hashlib.sha256(PDF[0][1][1]).hexdigest())
        self.assertEqual((rows[0].size, rows[0].storage_reference), (len(PDF[0][1][1]), ""))
        self.assertEqual(stored_files(self.settings.storage_root), [])

    def test_nostore_nothing_is_left_in_the_waiting_folder(self):
        self.make_client()
        self.post_api({"declaration_version": "1.0"})
        self.assertEqual(self.svc.requests.staging_bytes(), 0)

    def test_nostore_a_failed_request_does_not_leave_files_behind(self):
        self.make_client()

        def boom(*args, **kwargs):
            raise RuntimeError("falla simulada")
        self.svc.pipeline.execute_creation_flow = boom
        self.post_api({"declaration_version": "1.0"})
        self.assertEqual(self.count("evidence_requests"), 1)
        self.assertEqual(self.svc.requests.list_requests("FAILED")[0].status, "FAILED")
        self.assertEqual(self.svc.requests.staging_bytes(), 0)

    def test_nostore_storing_can_still_be_turned_on_for_development(self):
        self.make_client(store_originals=True)
        self.post_api({"declaration_version": "1.0"})
        self.assertEqual(len(stored_files(self.settings.storage_root)), 1)
        self.assertNotEqual(self.file_rows()[0].storage_reference, "")

    def test_nostore_a_video_over_the_limit_is_rejected_and_other_files_are_not_affected(self):
        self.make_client(max_video_mb=1)
        big = b"\x00" * (1024 * 1024 + 1)
        r = self.post_api({"declaration_version": "1.0"}, files=[("files", ("clip.mp4", big, "video/mp4"))], lang="en")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, "file.video_too_large"))
        self.assertIn("480p", r.json()["error"]["message"])
        self.assertEqual(self.count("evidence_requests"), 0)
        self.assertEqual(self.svc.requests.staging_bytes(), 0)
        ok = self.post_api({"declaration_version": "1.0"}, files=[("files", ("clip.mp4", b"\x00" * 1000, "video/mp4"))])
        self.assertEqual(ok.status_code, 200, ok.text)
        same_size_pdf = self.post_api({"declaration_version": "1.0"}, files=[("files", ("doc.pdf", big, "application/pdf"))], email="b@example.com")
        self.assertEqual(same_size_pdf.status_code, 200, same_size_pdf.text)

    def test_nostore_a_full_waiting_space_asks_to_wait_and_is_not_an_error_message(self):
        self.make_client(temp_budget_mb=0)
        es = self.post_api({"declaration_version": "1.0"}, lang="es")
        self.assertEqual((es.status_code, es.json()["error"]["code"]), (503, "busy"))
        self.assertIn("esperá unos minutos", es.json()["error"]["message"])
        self.assertIn("Tu información no se perdió", es.json()["error"]["message"])
        en = self.post_api({"declaration_version": "1.0-en"}, lang="en", email="b@example.com")
        self.assertIn("wait a few minutes", en.json()["error"]["message"])
        self.assertEqual(self.count("evidence_requests"), 0)

    def test_nostore_the_total_weight_of_a_request_is_capped(self):
        self.make_client(max_request_mb=1)
        half = b"\x00" * (600 * 1024)
        r = self.post_api({"declaration_version": "1.0"}, files=[("files", ("a.pdf", half, "application/pdf")), ("files", ("b.pdf", half, "application/pdf"))], lang="en")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, "request.too_large"))
        self.assertIn("1 MB total limit", r.json()["error"]["message"])
        self.assertEqual(self.count("evidence_requests"), 0)
        self.assertEqual(self.svc.requests.staging_bytes(), 0)
        ok = self.post_api({"declaration_version": "1.0"}, files=[("files", ("a.pdf", half, "application/pdf"))], email="b@example.com")
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_nostore_a_text_record_is_hashed_but_its_file_is_discarded(self):
        self.make_client()
        r = self.post_api({"declaration_version": "1.0", "text_content": "Una idea escrita directamente", "text_title": "Idea"}, files=[])
        self.assertEqual(r.status_code, 200, r.text)
        rows = self.file_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].storage_reference, rows[0].filename.endswith(".md")), ("", True))
        self.assertEqual(stored_files(self.settings.storage_root), [])

if __name__ == "__main__":
    unittest.main()
