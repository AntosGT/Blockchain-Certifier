import uuid
from typing import Any, Callable, Dict, List, Optional
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

from src.api.services import Services
from src.core.config import Settings
from src.core.creation_flow import DECLARATION_KIND, EvidenceValidationError, validate_hashed_file
from src.core.i18n import normalize_lang, tr
from src.core.rate_limiter import RateLimiter

def register_mcp(app: FastAPI, svc: Services, settings: Settings, client_ip: Callable[[Request], str],
                 check_upload_size: Callable[[str, int], None], ip_limiter: RateLimiter, email_limiter: RateLimiter,
                 global_limiter: RateLimiter, verify_limiter: RateLimiter, key_limiter: RateLimiter,
                 connect_global_limiter: RateLimiter) -> None:
    """
    MCP entry: it does not use the form or Turnstile (those belong to the browser) and the file never reaches the server.
    Off by default: with PROOF_MCP_ENABLED off, the whole entry answers 404.

    Identity: the client confirms their email once with the same email link as the portal and receives their own key.
      POST /api/mcp/conexion            {email, lang}  -> sends the confirmation link
      POST /api/mcp/conexion/confirmar  {t}            -> uses up the link and returns the key (shown only once)
    Declaration: accepted on every registration. The server returns the text with a signed receipt that the registration must carry:
    so nobody can register without requesting the declaration first. It does not prove a person read it; the skill enforces that.
      GET  /api/mcp/declaracion?lang=   -> {version, text, receipt}
    Registration: the key goes in Authorization: Bearer; the email comes from the key, not from the body.
      POST /api/mcp/registros  -> the MCP computes the fingerprint on the client's computer and sends name, size and fingerprint.
    The status of a registration is read with the existing route, GET /api/solicitudes/{id}?t=<access_token>.
    """
    receipts = URLSafeTimedSerializer(settings.session_secret, salt="prima-mcp-declaration")

    def api_error(code: str, message: str, status: int = 400):
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)

    def off():
        return api_error("not_found", "Not found.", 404) if not settings.mcp_enabled else None

    def unauthorized():
        return JSONResponse({"error": {"code": "auth.invalid", "message": "Unauthorized."}}, status_code=401,
                            headers={"WWW-Authenticate": "Bearer"})

    def key_email_of(request: Request) -> Optional[str]:
        scheme, _, given = request.headers.get("authorization", "").partition(" ")
        return svc.mcp_keys.authenticate(given.strip()) if scheme.lower() == "bearer" else None

    async def json_body(request: Request) -> Optional[Dict[str, Any]]:
        try:
            body = await request.json()
        except Exception:
            return None
        return body if isinstance(body, dict) else None

    @app.post("/api/mcp/conexion")
    async def mcp_connect(request: Request):
        if off():
            return off()
        body = await json_body(request)
        if body is None:
            return api_error("body.invalid", "The body must be a JSON object.")
        lang = normalize_lang(str(body.get("lang") or ""))
        email = str(body.get("email") or "").strip().lower()
        if not ip_limiter.allow(client_ip(request)):
            return api_error("rate.ip", tr(lang, "rate.ip"), 429)
        if not svc.request_service.valid_email(email):
            return api_error("email.invalid", tr(lang, "email.invalid"))
        if not email_limiter.allow(email) or not connect_global_limiter.allow("global"):
            return api_error("rate.email", tr(lang, "rate.email"), 429)
        svc.request_service.request_registration(email, lang, purpose="mcp")
        return {"ok": True, "sent_to": email, "link_hours": settings.email_link_ttl_hours}

    @app.post("/api/mcp/conexion/confirmar")
    async def mcp_confirm(request: Request):
        if off():
            return off()
        body = await json_body(request)
        if body is None:
            return api_error("body.invalid", "The body must be a JSON object.")
        if not verify_limiter.allow(client_ip(request)):
            return api_error("tries", tr("en", "tries.message"), 429)
        token = str(body.get("t") or "")
        lang = svc.request_service.verifications.lang_of(token, "es")
        email = svc.request_service.verifications.consume(token)
        if email is None:
            return api_error("link.invalid", tr(lang, "link.invalid.message"), 404)
        return {"key": svc.mcp_keys.create_key(email), "email": email}

    @app.get("/api/mcp/declaracion")
    def mcp_declaration(request: Request, lang: str = ""):
        if off():
            return off()
        key_email = key_email_of(request)
        if key_email is None:
            return unauthorized()
        lang = normalize_lang(lang)
        declaration = svc.legal.latest(DECLARATION_KIND, lang) or svc.legal.latest(DECLARATION_KIND)
        if not declaration:
            return api_error("declaration.missing_version", tr(lang, "declaration.missing_version"), 404)
        return {"version": declaration["version"], "text": declaration["text"],
                "receipt": receipts.dumps({"e": key_email, "v": declaration["version"]})}

    @app.post("/api/mcp/registros")
    async def mcp_register(request: Request):
        if off():
            return off()
        key_email = key_email_of(request)
        if key_email is None:
            return unauthorized()
        body = await json_body(request)
        if body is None:
            return api_error("body.invalid", "The body must be a JSON object.")
        lang = normalize_lang(str(body.get("lang") or ""))
        if not key_limiter.allow(key_email) or not global_limiter.allow("global"):
            return api_error("rate.key", tr(lang, "rate.email"), 429)
        raw = {k: body.get(k) for k in ("title", "category", "description", "name", "country", "document_id",
                                         "declaration_version")}
        raw = {k: (v if isinstance(v, str) else ("" if v is None else str(v))) for k, v in raw.items()}
        raw["email"] = key_email  # identity comes from the key, not from what the body says
        raw["declaration_accepted"] = body.get("declaration_accepted") is True
        fields = svc.request_service.normalize(raw)
        fields["lang"] = lang

        files: List[Dict[str, Any]] = body.get("files") if isinstance(body.get("files"), list) else []
        project = body.get("project")
        project = project.strip() if isinstance(project, str) else ""
        try:
            if not project or len(project) > 255:
                raise EvidenceValidationError.coded("project.required")
            if any(not isinstance(f, dict) or not isinstance(f.get("filename"), str) for f in files):
                raise EvidenceValidationError.coded("content.required")
            svc.request_service.validate_fields(fields, [f["filename"] for f in files])
            hashed = [{"filename": f["filename"], "sha256": str(f.get("sha256") or ""), "size": f.get("size")} for f in files]
            for f in hashed:
                validate_hashed_file(f)
                check_upload_size(f["filename"], f["size"])
            if sum(int(f["size"] or 0) for f in hashed) > settings.max_request_mb * 1024 * 1024:
                raise EvidenceValidationError.coded("request.too_large", mb=settings.max_request_mb)
            # The declaration is accepted on every registration: it needs the receipt of having requested it, for this client and this version
            try:
                shown = receipts.loads(str(body.get("declaration_receipt") or ""),
                                       max_age=settings.mcp_declaration_receipt_minutes * 60)
            except BadSignature:
                shown = None
            if not isinstance(shown, dict) or shown.get("e") != key_email or shown.get("v") != fields["declaration_version"]:
                raise EvidenceValidationError.coded("declaration.not_shown")
            req, access_token = svc.request_service.submit_hashes(str(uuid.uuid4()), fields, hashed, project)
        except EvidenceValidationError as e:
            return api_error(e.code or "invalid", e.message(lang))
        return JSONResponse({"id": str(req.id), "access_token": access_token, "status": req.status,
                             "evidence_id": req.evidence_id}, status_code=201)
