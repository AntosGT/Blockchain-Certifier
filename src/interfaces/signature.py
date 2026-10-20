from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class SignatureProvider(ABC):
    """
    Abstract interface for digital / cryptographic signature providers.
    Allows integrating Argentine Digital Signature (ONTI / X.509), HSMs or internal signatures.
    """

    @abstractmethod
    def sign_manifest(self, canonical_manifest_bytes: bytes) -> Dict[str, Any]:
        """
        Receives the bytes of the canonicalized manifest (RFC 8785) and creates the signature object.
        Returns a dictionary with the Base64 signature, algorithm, certificate and timestamp.
        """
        pass

    @abstractmethod
    def verify_signature(self, canonical_manifest_bytes: bytes, signature_data: Dict[str, Any]) -> bool:
        """
        Cryptographically verifies that the signature matches the manifest bytes exactly.
        """
        pass
