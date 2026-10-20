import hmac
import hashlib
import base64
from typing import Dict, Any
from src.interfaces.signature import SignatureProvider
from src.core.manifest import format_iso8601_utc

class InternalHmacSignatureProvider(SignatureProvider):
    """
    Concrete SignatureProvider implementation using internal HMAC-SHA256.
    It seals the canonical manifest with the system's private key.
    """

    def __init__(self, secret_key: str = "PROOF_INTERNAL_SYSTEM_SECRET_KEY_CHANGE_IN_PROD"):
        self.secret_bytes = secret_key.encode("utf-8")
        self.algorithm = "HMAC-SHA256"
        self.key_id = "proof-internal-v1"

    def sign_manifest(self, canonical_manifest_bytes: bytes) -> Dict[str, Any]:
        signed_at = format_iso8601_utc()
        
        # Compute HMAC-SHA256 over the manifest bytes
        signature_bytes = hmac.new(self.secret_bytes, canonical_manifest_bytes, hashlib.sha256).digest()
        signature_b64 = base64.b64encode(signature_bytes).decode("utf-8")
        
        return {
            "algorithm": self.algorithm,
            "key_id": self.key_id,
            "signature_b64": signature_b64,
            "signed_at": signed_at
        }

    def verify_signature(self, canonical_manifest_bytes: bytes, signature_data: Dict[str, Any]) -> bool:
        expected_signature_b64 = signature_data.get("signature_b64")
        if not expected_signature_b64:
            return False

        recalculated_bytes = hmac.new(self.secret_bytes, canonical_manifest_bytes, hashlib.sha256).digest()
        recalculated_b64 = base64.b64encode(recalculated_bytes).decode("utf-8")
        
        # Constant-time comparison to prevent timing attacks
        return hmac.compare_digest(recalculated_b64, expected_signature_b64)
