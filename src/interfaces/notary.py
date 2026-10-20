from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class NotaryProvider(ABC):
    """
    Abstract interface for notarial intervention (Evidence+).
    Allows requesting electronic or scanned notarial certificates and linking them to the record.
    """

    @abstractmethod
    def create_notarial_request(self, evidence_id: str, notary_id: str, metadata: Dict[str, Any]) -> str:
        """
        Starts a notarial verification procedure and returns the unique id of the request.
        """
        pass

    @abstractmethod
    def attach_notarial_document(self, request_id: str, document_bytes: bytes, metadata: Dict[str, Any]) -> bool:
        """
        Links the signed notarial certificate or document to the registered evidence.
        """
        pass
