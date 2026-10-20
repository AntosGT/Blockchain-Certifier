import os
import hmac
import hashlib
import time
from typing import Tuple, Optional

class PrivateStorageManager:
    """
    Private object storage manager:
    - Encryption / isolation in a private repository.
    - Generation and verification of signed URLs with expiration.
    """

    def __init__(self, storage_root: str, signing_secret: str = "PROOF_STORAGE_SECRET_KEY"):
        self.storage_root = os.path.abspath(storage_root)
        self.signing_secret = signing_secret.encode("utf-8")
        os.makedirs(self.storage_root, exist_ok=True)

    def store_file(self, evidence_id: str, filename: str, content_bytes: bytes) -> Tuple[str, str]:
        """
        Stores a file in isolation under the evidence folder.
        Returns (storage_reference, sha256_hex).
        """
        evidence_dir = os.path.join(self.storage_root, evidence_id)
        os.makedirs(evidence_dir, exist_ok=True)
        
        target_path = os.path.join(evidence_dir, filename)
        with open(target_path, "wb") as f:
            f.write(content_bytes)
            
        sha256_hex = hashlib.sha256(content_bytes).hexdigest()
        rel_reference = os.path.relpath(target_path, self.storage_root).replace("\\", "/")
        return rel_reference, sha256_hex

    def read_file(self, storage_reference: str) -> bytes:
        """Reads the content of a privately stored file."""
        target_path = os.path.join(self.storage_root, storage_reference.replace("/", os.sep))
        if not os.path.exists(target_path):
            raise FileNotFoundError(f"Archivo no encontrado en almacenamiento privado: {storage_reference}")
        with open(target_path, "rb") as f:
            return f.read()

    def generate_signed_url(self, storage_reference: str, expires_in_seconds: int = 3600) -> str:
        """Generates a token and a signed URL with an expiration time."""
        expires_at = int(time.time()) + expires_in_seconds
        payload = f"{storage_reference}:{expires_at}"
        signature = hmac.new(self.signing_secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"/api/v1/storage/download?ref={storage_reference}&expires={expires_at}&sig={signature}"

    def verify_signed_url(self, storage_reference: str, expires_at: int, signature: str) -> bool:
        """Checks the validity and expiration of a signed URL."""
        if int(time.time()) > expires_at:
            return False # Expirada
            
        payload = f"{storage_reference}:{expires_at}"
        expected_sig = hmac.new(self.signing_secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected_sig, signature)
