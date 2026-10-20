import re
import shutil
import uuid
from typing import Any, Dict, List, Optional, Tuple
from src.core.config import Settings
from src.core.countries import COUNTRY_CODES
from src.core.creation_flow import (CATEGORIES, DECLARATION_KIND, EvidenceCreationPipeline, EvidenceValidationError,
                                    validate_filename)
from src.core.email_verifications import EmailVerificationManager
from src.core.evidence_requests import RequestManager
from src.core.legal import LegalVersionManager
from src.core.notification_events import NotificationEvents
from src.core.users import UserManager
from src.models.entities import EvidenceRequestSchema, UserSchema

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")

class RequestExpiredError(Exception):
    """The verification link expired."""

class RequestService:
    """
    Registration request -> emailed link (confirms the email) -> form -> evidence.
    The form only opens with the emailed link, and submitting it creates the evidence right away.
    """

    def __init__(self, settings: Settings, requests: RequestManager, users: UserManager,
                 pipeline: EvidenceCreationPipeline, legal: LegalVersionManager, events: NotificationEvents,
                 verifications: EmailVerificationManager):
        self.verifications = verifications
        self.settings = settings
        self.requests = requests
        self.users = users
        self.pipeline = pipeline
        self.legal = legal
        self.events = events

    # Alta -------------------------------------------------------------------------

    @staticmethod
    def normalize(fields: Dict[str, Any]) -> Dict[str, Any]:
        f = {k: (v.strip() if isinstance(v, str) else v) for k, v in fields.items()}
        f["email"] = (f.get("email") or "").lower()
        f["country"] = (f.get("country") or "").upper()
        for optional in ("document_id", "text_title", "text_content", "text_authors"):
            f[optional] = f.get(optional) or None
        f["description"] = f.get("description") or ""
        return f

    @staticmethod
    def valid_email(email: str) -> bool:
        return bool(_EMAIL_RE.match(email)) and len(email) <= 255

    def validate_fields(self, fields: Dict[str, Any], filenames: List[str]) -> None:
        """Validates the form. Errors carry a code: the portal shows them in the client's language."""
        coded = EvidenceValidationError.coded
        if not fields.get("title"):
            raise coded("title.required")
        if len(fields["title"]) > 255:
            raise coded("title.too_long")
        if fields.get("category") not in CATEGORIES:
            raise coded("category.required")
        if len(fields.get("name") or "") < 2:
            raise coded("name.required")
        if not _EMAIL_RE.match(fields["email"]) or len(fields["email"]) > 255:
            raise coded("email.invalid")
        if fields["country"] not in COUNTRY_CODES:
            raise coded("country.required")
        if fields.get("document_id") and len(fields["document_id"]) > 64:
            raise coded("document.too_long")
        if not self.legal.get(DECLARATION_KIND, fields.get("declaration_version") or ""):
            raise coded("declaration.missing_version")
        if not fields.get("declaration_accepted"):
            raise coded("declaration.required")
        if not filenames and not fields.get("text_content"):
            raise coded("content.required")
        if len(filenames) > self.settings.max_files_per_request:
            raise coded("files.too_many", max=self.settings.max_files_per_request)
        seen = set()
        for name in filenames:
            validate_filename(name)
            if name.lower() in seen:
                raise coded("file.duplicate", name=repr(name))
            seen.add(name.lower())

    def new_request_id(self) -> str:
        return str(uuid.uuid4())

    # Email confirmation (before the form) -----------------------------------

    def request_registration(self, email: str, lang: str = "en", purpose: Optional[str] = None) -> None:
        """Registration request: creates the link (expires after 24 h) and queues the email that carries it, in the client's language."""
        verification_id, token = self.verifications.create(email, self.settings.email_link_ttl_hours, lang)
        self.events.email_confirmation(verification_id, email, token, lang, purpose)

    def open_form(self, token: str) -> Optional[str]:
        """Opens the emailed link: confirms the email and returns it, or None if it does not work."""
        return self.verifications.open(token)

    def verified_email(self, token: str) -> Optional[str]:
        return self.verifications.peek(token)

    # Request and generation ----------------------------------------------------------

    def submit(self, request_id: str, fields: Dict[str, Any], staged_files: List[Dict[str, Any]],
               link_token: str) -> Tuple[EvidenceRequestSchema, str]:
        """
        Uses up the emailed link, records the request (with its files already in staging) and creates the evidence right away.
        The email comes from the link, not from the form. If generation fails, the request stays FAILED for the admin.
        Returns (request, access token).
        """
        email = self.verifications.consume(link_token)
        if email is None:
            raise LookupError("El enlace no sirve o ya se usó.")
        fields = dict(fields, email=email)
        try:
            request, verification_token, access_token = self.requests.create(
                request_id, fields, staged_files, self.settings.verification_ttl_hours)
        except Exception:
            # The request was not saved: the client lost nothing, so the link is given back
            self.verifications.release(link_token)
            raise
        # From here on the request exists (if something fails it stays FAILED for the admin) and the link is used up
        self.events.request_received(request)
        try:
            request = self.process(verification_token)
        except Exception:
            request = self.requests.get(request_id)  # process() already marked it FAILED
        return request, access_token

    def submit_hashes(self, request_id: str, fields: Dict[str, Any], hashed_files: List[Dict[str, Any]],
                      project: str) -> Tuple[EvidenceRequestSchema, str]:
        """
        Registration from the MCP: fingerprints arrive already computed on the client's computer, never the file. There is no
        emailed link to use up. From here on it follows the same stages as the portal (evidence, timestamp, certificate, notifications).
        """
        request, verification_token, access_token = self.requests.create(
            request_id, fields, [], self.settings.verification_ttl_hours, source="mcp", project=project)
        self.events.request_received(request)
        try:
            request = self.process(verification_token, hashed_files)
        except Exception:
            request = self.requests.get(request_id)  # process() already marked it FAILED
        return request, access_token

    def process(self, token: str, hashed_files: Optional[List[Dict[str, Any]]] = None) -> EvidenceRequestSchema:
        request = self.requests.get_by_verification_token(token)
        if request is None:
            raise LookupError("Enlace de verificación no válido.")
        if request.status == "PROCESSED":
            return request  # idempotente: no genera otra evidencia
        if not self.requests.claim_for_processing(request.id):
            current = self.requests.get(request.id)
            if current.status == "PENDING_EMAIL":
                self.requests.mark_expired(request.id)
                raise RequestExpiredError("El enlace de verificación venció.")
            return current  # another process already took it

        try:
            stored = self.users.get_by_email(request.email)
            if stored is None:
                stored = self.users.create_user(UserSchema(
                    email=request.email, name=request.name, country=request.country, document_id=request.document_id))
            self.users.mark_email_verified(stored.id)
            declared = stored.model_copy(update={
                "name": request.name, "country": request.country, "document_id": request.document_id,
                "email_verified": True})

            files_input = []
            for f in self.requests.get_files(request.id):
                with open(f["storage_path"], "rb") as fh:
                    files_input.append((f["filename"], fh.read()))
            text_input = None
            if request.text_content:
                text_input = {"title": request.text_title or request.title, "content": request.text_content,
                              "authors": request.text_authors or request.name}

            evidence, _, _ = self.pipeline.execute_creation_flow(
                user=declared, title=request.title, category=request.category, description=request.description,
                files_input=files_input, text_input=text_input, declaration_accepted=True,
                declaration_version=request.declaration_version, payment_reference=f"request:{request.id}",
                defer_timestamp=True, hashed_files=hashed_files)
        except Exception as e:
            self.requests.mark_failed(request.id, f"{type(e).__name__}: {e}")
            shutil.rmtree(f"{self.requests.staging_root}/{request.id}", ignore_errors=True)  # never leave user files on the server
            raise
        self.requests.mark_processed(request.id, evidence.id)
        shutil.rmtree(f"{self.requests.staging_root}/{request.id}", ignore_errors=True)
        return self.requests.get(request.id)
