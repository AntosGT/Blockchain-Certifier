import unittest
import hashlib
from src.core.manifest import canonicalize_json, build_manifest_dict

class TestCanonicalization(unittest.TestCase):

    def test_canonicalization_determinism(self):
        """Tests that keys are sorted alphabetically and the bytes are identical regardless of the original order."""
        data1 = {
            "title": "Prueba",
            "evidence_id": "EV-001",
            "category": "investigacion",
            "owner": {"name": "Owner", "country": "AR", "email": "owner@example.com"}
        }

        data2 = {
            "owner": {"email": "owner@example.com", "name": "Owner", "country": "AR"},
            "category": "investigacion",
            "evidence_id": "EV-001",
            "title": "Prueba"
        }
        
        bytes1 = canonicalize_json(data1)
        bytes2 = canonicalize_json(data2)
        
        # They must be identical byte for byte
        self.assertEqual(bytes1, bytes2)
        self.assertEqual(hashlib.sha256(bytes1).hexdigest(), hashlib.sha256(bytes2).hexdigest())

    def test_manifest_builder(self):
        """Tests building the manifest and its canonicalization."""
        files = [
            {"filename": "doc2.pdf", "sha256": "bbb", "size": 200, "mime": "application/pdf"},
            {"filename": "doc1.pdf", "sha256": "aaa", "size": 100, "mime": "application/pdf"}
        ]
        
        manifest = build_manifest_dict(
            evidence_id="EV-20261001-TEST",
            title="Documento de Prueba",
            category="investigacion",
            owner_name="Test Owner",
            owner_email="owner@example.com",
            owner_country="AR",
            files_metadata=files,
            created_at_iso="2026-10-01T10:00:00.000Z"
        )
        
        # Check that the files are sorted by filename
        self.assertEqual(manifest["files"][0]["filename"], "doc1.pdf")
        self.assertEqual(manifest["files"][1]["filename"], "doc2.pdf")
        
        canonical_bytes = canonicalize_json(manifest)
        self.assertIn(b"\"created_at\":\"2026-10-01T10:00:00.000Z\"", canonical_bytes)

if __name__ == "__main__":
    unittest.main()
