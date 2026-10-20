import mimetypes
import os
from pathlib import Path
from typing import Dict, List, Optional
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from src.api.admin import register_admin
from src.api.internal import register_internal
from src.api.mcp_entry import register_mcp
from src.api.services import Services, build_services
from src.core.bot_protection import FormGuard, TurnstileVerifier
from src.core.config import Settings, get_settings
from src.core.countries import COUNTRIES, COUNTRIES_EN
from src.core.i18n import DEFAULT_LANG, MESSAGES, normalize_lang, tr
from src.core.rate_limiter import RateLimiter
from src.core.labels import CATEGORY_LABELS, category_labels
from src.core.creation_flow import CATEGORIES, DECLARATION_KIND, EvidenceValidationError

mimetypes.add_type("font/woff2", ".woff2")
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_PATHS = {"/favicon.ico"}
VIDEO_EXTENSIONS = {".mp4", ".mov"}

TURNSTILE_ORIGIN = "https://challenges.cloudflare.com"

def create_app(settings: Optional[Settings] = None, services: Optional[Services] = None,
               turnstile: Optional[TurnstileVerifier] = None) -> FastAPI:
    settings = settings or (services.settings if services else get_settings())
    svc = services or build_services(settings)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.globals["tr"] = tr  # templates request their texts by key: tr(lang, "key")
    submit_limiter = RateLimiter(svc.engine, "submit", settings.submit_rate_per_hour)
    verify_limiter = RateLimiter(svc.engine, "verify", settings.verify_rate_per_hour)
    email_limiter = RateLimiter(svc.engine, "email", settings.submit_rate_per_email_hour)
    global_limiter = RateLimiter(svc.engine, "global", settings.submit_global_per_hour)
    max_bytes = settings.max_file_mb * 1024 * 1024
    video_bytes = settings.max_video_mb * 1024 * 1024
    temp_budget_bytes = settings.temp_budget_mb * 1024 * 1024
    request_bytes = settings.max_request_mb * 1024 * 1024

    def check_upload_size(filename: str, size: int) -> None:
        """Free service limit: 50 MB per file and, for video (480p), the max_video_mb cap."""
        if size > max_bytes:
            raise EvidenceValidationError.coded("file.too_large", name=repr(filename), mb=settings.max_file_mb)
        if os.path.splitext(filename)[1].lower() in VIDEO_EXTENSIONS and size > video_bytes:
            raise EvidenceValidationError.coded("file.video_too_large", name=repr(filename), mb=settings.max_video_mb)

    def saturated() -> bool:
        """The temporary space for files in transit is full: the client is asked to wait a few minutes."""
        return svc.requests.staging_bytes() >= temp_budget_bytes

    turnstile_on = bool(settings.turnstile_site_key and settings.turnstile_secret_key)
    verifier = turnstile or (TurnstileVerifier(settings.turnstile_secret_key) if turnstile_on else None)
    form_guard = FormGuard(settings.session_secret, settings.form_min_seconds) if settings.form_guard_enabled else None
    csp = "default-src 'self'; style-src 'self' 'unsafe-inline'; form-action 'self'"
    if turnstile_on:
        csp += f"; script-src 'self' {TURNSTILE_ORIGIN}; frame-src {TURNSTILE_ORIGIN}; connect-src 'self' {TURNSTILE_ORIGIN}"

    app = FastAPI(title="prima-certified", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.services = svc
    login_limiter = RateLimiter(svc.engine, "login", settings.login_rate_per_hour)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # Links carry a token in the URL: it must never travel in the Referer
        response.headers["Referrer-Policy"] = "no-referrer"
        # Pages carry tokens and are not cached; icons are public and are cached
        response.headers["Cache-Control"] = "public, max-age=86400" if request.url.path in STATIC_PATHS or request.url.path.startswith("/static/") else "no-store"
        response.headers["Content-Security-Policy"] = csp
        return response

    def render(request: Request, name: str, status_code: int = 200, **ctx):
        return templates.TemplateResponse(request, name, ctx, status_code=status_code)

    trusted_proxies = {ip.strip() for ip in settings.trusted_proxy_ips.split(",") if ip.strip()}

    def client_ip(request: Request) -> str:
        peer = request.client.host if request.client else "unknown"
        if settings.proxy_ip_header and peer in trusted_proxies:
            forwarded = request.headers.get(settings.proxy_ip_header, "").split(",")[0].strip()
            if forwarded:
                return forwarded
        return peer

    def bot_widget() -> dict:
        """What the templates need to show the challenge and the form timestamp."""
        return {"turnstile_site_key": settings.turnstile_site_key if turnstile_on else "",
                "form_token": form_guard.issue() if form_guard else ""}

    def bot_problem(request: Request, honeypot: str, form_token: str, turnstile_token: str) -> Optional[str]:
        """Rejection message (in Spanish: used by the dashboard login) if the submission fails the bot defenses, or None.
        Cheap checks first, Turnstile last. The public portal shows its own text in the client's language."""
        message = tr("es", "bot.failed")
        if form_guard and not form_guard.check(form_token, honeypot):
            return message
        if verifier and not verifier.verify(turnstile_token, client_ip(request)):
            return message
        return None

    def form_context(lang: str, **extra):
        """What the client pages need, in their language: texts, categories, countries and the declaration in that language."""
        lang = normalize_lang(lang)
        declaration = svc.legal.latest(DECLARATION_KIND, lang) or svc.legal.latest(DECLARATION_KIND)
        labels = category_labels(lang)
        base = {
            "lang": lang, "home_url": "/es" if lang == "es" else "/",
            "categories": [(c, labels.get(c, c)) for c in sorted(CATEGORIES, key=lambda c: labels.get(c, c))],
            "countries": COUNTRIES if lang == "es" else COUNTRIES_EN,
            "declaration": declaration, "price_usd": settings.price_usd, "payment_mode": settings.payment_mode,
            "link_hours": settings.email_link_ttl_hours, "max_files": settings.max_files_per_request, "max_file_mb": settings.max_file_mb,
            "allowed_extensions": "PDF, DOCX, TXT, MD, JPG, PNG, WEBP, MP4, MOV, MP3, WAV, ZIP",
            "values": {}, "error": None,
        }
        base.update(bot_widget())
        base.update(extra)
        return base

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/x-icon")

    @app.get("/salud")
    def health():
        with svc.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    def error_page(request: Request, lang: str, status: int, title_key: str, message_key: str):
        lang = normalize_lang(lang)
        return render(request, "error.html", status_code=status, lang=lang, home_url="/es" if lang == "es" else "/",
                      title=tr(lang, title_key), message=tr(lang, message_key))

    @app.get("/")
    def index(request: Request):
        # Like fragola.agency: English is the main page and Spanish lives under /es
        return render(request, "index_en.html", **form_context("en"))

    @app.get("/es")
    def index_es(request: Request):
        return render(request, "index.html", **form_context("es"))

    @app.post("/solicitar-registro")
    def request_registration(
        request: Request, email: str = Form(""), hp_trap_field: str = Form(""), form_token: str = Form(""),
        turnstile_token: str = Form("", alias="cf-turnstile-response"), lang: str = Form(""),
    ):
        email = email.strip().lower()
        lang = "es" if lang == "es" else "en"
        page = "index.html" if lang == "es" else "index_en.html"

        def reject(message_key: str, status: int = 400):
            return render(request, page, status_code=status, **form_context(lang, values={"email": email}, error=tr(lang, message_key)))

        if not submit_limiter.allow(client_ip(request)):
            return reject("rate.ip", 429)
        if bot_problem(request, hp_trap_field, form_token, turnstile_token):
            return reject("bot.failed")
        if not svc.request_service.valid_email(email):
            return reject("email.invalid")
        # Per-recipient cap and global cap: otherwise the portal could be used to send emails to a third party
        if not email_limiter.allow(email) or not global_limiter.allow("global"):
            return reject("rate.email", 429)
        svc.request_service.request_registration(email, lang)
        return render(request, page, **form_context(lang, sent_to=email))

    @app.get("/formulario")
    def registration_form(request: Request, t: str = ""):
        # The language comes from the link, even if it expired; if unknown, the site's language (English)
        lang = svc.request_service.verifications.lang_of(t, DEFAULT_LANG)
        if not verify_limiter.allow(client_ip(request)):
            return error_page(request, lang, 429, "tries.title", "tries.message")
        email = svc.request_service.open_form(t)
        if email is None:
            return error_page(request, lang, 404, "link.invalid.title", "link.invalid.message")
        return render(request, "formulario.html", **form_context(lang, email=email, token=t))

    @app.post("/solicitudes")
    async def submit(
        request: Request,
        title: str = Form(""), category: str = Form(""), description: str = Form(""),
        name: str = Form(""), country: str = Form(""), document_id: str = Form(""),
        text_title: str = Form(""), text_content: str = Form(""), text_authors: str = Form(""),
        declaration_version: str = Form(""), declaration_accepted: Optional[str] = Form(None),
        t: str = Form(""),
        files: List[UploadFile] = File(default=[]),
    ):
        lang = svc.request_service.verifications.lang_of(t, DEFAULT_LANG)
        email = svc.request_service.verified_email(t)
        if email is None:
            return error_page(request, lang, 404, "link.invalid.title", "link.invalid.message")
        raw = {"title": title, "category": category, "description": description, "name": name, "email": email,
               "country": country, "document_id": document_id, "text_title": text_title,
               "text_content": text_content, "text_authors": text_authors,
               "declaration_version": declaration_version, "declaration_accepted": bool(declaration_accepted)}
        fields = svc.request_service.normalize(raw)

        def reject(error: EvidenceValidationError, status: int = 400):
            return render(request, "formulario.html", status_code=status,
                          **form_context(lang, values=fields, error=error.message(lang), email=email, token=t))

        fields["lang"] = lang
        uploads = [f for f in files if f.filename]
        try:
            svc.request_service.validate_fields(fields, [f.filename for f in uploads])
        except EvidenceValidationError as e:
            return reject(e)
        if saturated():
            return render(request, "formulario.html", status_code=503,
                          **form_context(lang, values=fields, error=tr(lang, "busy"), email=email, token=t))

        request_id = svc.request_service.new_request_id()
        staged: List[Dict] = []
        try:
            total = 0
            for upload in uploads:
                path = svc.requests.stage_path(request_id, upload.filename)
                size = 0
                with open(path, "wb") as out:
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        total += len(chunk)
                        check_upload_size(upload.filename, size)
                        if total > request_bytes:
                            raise EvidenceValidationError.coded("request.too_large", mb=settings.max_request_mb)
                        out.write(chunk)
                if size == 0:
                    raise EvidenceValidationError.coded("file.empty", name=repr(upload.filename))
                staged.append({"filename": upload.filename, "size": size, "storage_path": path})
            req, access_token = svc.request_service.submit(request_id, fields, staged, t)
        except EvidenceValidationError as e:
            _discard_staging(svc, request_id)
            return reject(e)
        except LookupError:
            _discard_staging(svc, request_id)  # the link was used by another submission while the files were uploading
            return error_page(request, lang, 404, "link.invalid.title", "link.invalid.message")
        except Exception:
            _discard_staging(svc, request_id)
            raise
        return RedirectResponse(f"/solicitudes/{req.id}?t={access_token}", status_code=303)

    def load_request(request_id: str, token: str):
        if not token:
            return None
        try:
            return svc.requests.get_with_access_token(request_id, token)
        except Exception:
            return None  # id with an invalid format

    @app.get("/solicitudes/{request_id}")
    def status_page(request: Request, request_id: str, t: str = ""):
        req = load_request(request_id, t)
        if req is None:
            return error_page(request, DEFAULT_LANG, 404, "request.notfound.title", "request.notfound.message")
        lang = normalize_lang(req.lang)  # the language in which the client requested the registration
        evidence = svc.evidence.get_evidence(req.evidence_id) if req.evidence_id else None
        return render(request, "status.html", lang=lang, home_url="/es" if lang == "es" else "/", req=req, token=t, evidence=evidence,
                      evidence_text=tr(lang, f"evidence.{evidence.status.value}") if evidence else "",
                      category_label=category_labels(lang).get(req.category, req.category),
                      ttl_hours=settings.verification_ttl_hours)

    # Portal JSON API (consumed by the static frontend) -------------------------------------------------

    def api_error(code: str, message: str, status: int = 400):
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)

    def link_invalid(lang: str):
        return api_error("link.invalid", tr(lang, "link.invalid.message"), 404)

    @app.get("/api/config")
    def api_config(lang: str = "en"):
        """Everything the frontend needs to build the form in one language, plus a fresh timestamp for the guard."""
        ctx = form_context(lang)
        declaration = ctx["declaration"]
        return {
            "lang": ctx["lang"],
            "categories": [{"value": value, "label": label} for value, label in ctx["categories"]],
            "countries": [{"code": code, "name": name} for code, name in ctx["countries"]],
            "declaration": {"version": declaration["version"], "text": declaration["text"]},
            "max_files": ctx["max_files"], "max_file_mb": ctx["max_file_mb"], "max_video_mb": settings.max_video_mb,
            "allowed_extensions": ctx["allowed_extensions"],
            "link_hours": ctx["link_hours"], "payment_mode": ctx["payment_mode"], "price_usd": ctx["price_usd"],
            "turnstile_site_key": ctx["turnstile_site_key"], "form_token": ctx["form_token"],
        }

    @app.get("/api/i18n")
    def api_i18n(lang: str = "en"):
        lang = normalize_lang(lang)
        return {key: versions[lang] for key, versions in MESSAGES.items()}

    @app.post("/api/registro")
    def api_request_registration(
        request: Request, email: str = Form(""), hp_trap_field: str = Form(""), form_token: str = Form(""),
        turnstile_token: str = Form("", alias="cf-turnstile-response"), lang: str = Form(""),
    ):
        email = email.strip().lower()
        lang = "es" if lang == "es" else "en"
        if not submit_limiter.allow(client_ip(request)):
            return api_error("rate.ip", tr(lang, "rate.ip"), 429)
        if bot_problem(request, hp_trap_field, form_token, turnstile_token):
            return api_error("bot.failed", tr(lang, "bot.failed"))
        if not svc.request_service.valid_email(email):
            return api_error("email.invalid", tr(lang, "email.invalid"))
        if not email_limiter.allow(email) or not global_limiter.allow("global"):
            return api_error("rate.email", tr(lang, "rate.email"), 429)
        svc.request_service.request_registration(email, lang)
        return {"ok": True, "sent_to": email, "link_hours": settings.email_link_ttl_hours}

    @app.get("/api/formulario")
    def api_registration_form(request: Request, t: str = ""):
        lang = svc.request_service.verifications.lang_of(t, DEFAULT_LANG)
        if not verify_limiter.allow(client_ip(request)):
            return api_error("tries", tr(lang, "tries.message"), 429)
        email = svc.request_service.open_form(t)
        if email is None:
            return link_invalid(lang)
        return {"email": email, "lang": lang}

    @app.post("/api/solicitudes")
    async def api_submit(
        request: Request,
        title: str = Form(""), category: str = Form(""), description: str = Form(""),
        name: str = Form(""), country: str = Form(""), document_id: str = Form(""),
        text_title: str = Form(""), text_content: str = Form(""), text_authors: str = Form(""),
        declaration_version: str = Form(""), declaration_accepted: Optional[str] = Form(None),
        t: str = Form(""),
        files: List[UploadFile] = File(default=[]),
    ):
        lang = svc.request_service.verifications.lang_of(t, DEFAULT_LANG)
        email = svc.request_service.verified_email(t)
        if email is None:
            return link_invalid(lang)
        raw = {"title": title, "category": category, "description": description, "name": name, "email": email,
               "country": country, "document_id": document_id, "text_title": text_title,
               "text_content": text_content, "text_authors": text_authors,
               "declaration_version": declaration_version, "declaration_accepted": bool(declaration_accepted)}
        fields = svc.request_service.normalize(raw)
        fields["lang"] = lang
        uploads = [f for f in files if f.filename]
        try:
            svc.request_service.validate_fields(fields, [f.filename for f in uploads])
        except EvidenceValidationError as e:
            return api_error(e.code or "invalid", e.message(lang))
        if saturated():
            return api_error("busy", tr(lang, "busy"), 503)

        request_id = svc.request_service.new_request_id()
        staged: List[Dict] = []
        try:
            total = 0
            for upload in uploads:
                path = svc.requests.stage_path(request_id, upload.filename)
                size = 0
                with open(path, "wb") as out:
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        total += len(chunk)
                        check_upload_size(upload.filename, size)
                        if total > request_bytes:
                            raise EvidenceValidationError.coded("request.too_large", mb=settings.max_request_mb)
                        out.write(chunk)
                if size == 0:
                    raise EvidenceValidationError.coded("file.empty", name=repr(upload.filename))
                staged.append({"filename": upload.filename, "size": size, "storage_path": path})
            req, access_token = svc.request_service.submit(request_id, fields, staged, t)
        except EvidenceValidationError as e:
            _discard_staging(svc, request_id)
            return api_error(e.code or "invalid", e.message(lang))
        except LookupError:
            _discard_staging(svc, request_id)
            return link_invalid(lang)
        except Exception:
            _discard_staging(svc, request_id)
            raise
        return {"id": str(req.id), "access_token": access_token, "status_path": f"/solicitudes/{req.id}?t={access_token}"}

    @app.get("/api/solicitudes/{request_id}")
    def api_status(request_id: str, t: str = ""):
        req = load_request(request_id, t)
        if req is None:
            return api_error("request.notfound", tr(DEFAULT_LANG, "request.notfound.message"), 404)
        lang = normalize_lang(req.lang)
        evidence = svc.evidence.get_evidence(req.evidence_id) if req.evidence_id else None
        return {
            "lang": lang, "status": req.status, "title": req.title, "name": req.name, "email": req.email,
            "category_label": category_labels(lang).get(req.category, req.category),
            "created_at": req.created_at.isoformat(), "evidence_id": req.evidence_id,
            "declaration_version": evidence.declaration_version if evidence else None,
            "manifest_hash": evidence.manifest_hash if evidence else None,
            "evidence_text": tr(lang, f"evidence.{evidence.status.value}") if evidence else "",
        }

    register_admin(app, svc, templates, settings, CATEGORY_LABELS, client_ip, login_limiter, bot_problem, bot_widget)
    register_internal(app, svc, settings)
    # The MCP connection sends emails without Turnstile: its global cap is separate, so it does not exhaust the web one
    register_mcp(app, svc, settings, client_ip, check_upload_size, submit_limiter, email_limiter, global_limiter,
                 verify_limiter, RateLimiter(svc.engine, "mcp", settings.mcp_rate_per_hour),
                 RateLimiter(svc.engine, "mcp_connect_global", settings.submit_global_per_hour))
    return app

def _discard_staging(svc: Services, request_id: str) -> None:
    import shutil
    shutil.rmtree(os.path.join(svc.requests.staging_root, request_id), ignore_errors=True)
