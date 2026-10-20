import os
import shutil
import tempfile
import unittest
from src.core.hasher import compute_file_sha256, generate_salt_hex
from src.core.text_evidence import create_text_evidence_file

class TestEvidenceCore(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_streaming_hash_large_simulated_file(self):
        """Tests that streaming hashing works correctly with files."""
        file_path = os.path.join(self.test_dir, "large_file.bin")
        # Create a 1 MB file
        with open(file_path, "wb") as f:
            f.write(b"A" * (1024 * 1024))
            
        sha256_hex, size = compute_file_sha256(file_path)
        self.assertEqual(size, 1024 * 1024)
        self.assertEqual(len(sha256_hex), 64)

    def test_text_evidence_creation(self):
        """Tests turning plain text into an immutable document with LF line breaks."""
        file_path, sha256_hex, size = create_text_evidence_file(
            title="Mi Gran Idea",
            content="Esta es la descripción detallada de la invención.",
            description="Proyecto secreto",
            authors="Test Author",
            output_dir=self.test_dir
        )
        self.assertTrue(os.path.exists(file_path))
        self.assertGreater(size, 0)
        
        # Check that the file has no \r (Windows line breaks)
        with open(file_path, "rb") as f:
            content_bytes = f.read()
        self.assertNotIn(b"\r\n", content_bytes)

if __name__ == "__main__":
    unittest.main()
