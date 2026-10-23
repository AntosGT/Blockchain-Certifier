import json
import os
import re
import secrets
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from src.api.services import Services
from src.core.admin_auth import DUMMY_HASH, verify_password
from src.core.config import Settings
from src.core.control import overdue_operations
from src.core.labels import STAGES, STAGE_LABELS

COOKIE = "prima_admin"

# Evidence ids are always generated as "<prefix>-<YYYYMMDD>-<8 hex>" (see
# creation_flow.py); reject anything else before it reaches a redirect, so an
# untrusted path segment can never carry a scheme or host (py/url-redirection).
EVIDENCE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

REQUEST_STATES = {
    "PENDING_EMAIL": "Esperando confirmación de email", "AWAITING_PAYMENT": "Esperando pago",
    "PROCESSING": "Procesando", "PROCESSED": "Procesada", "FAILED": "Fallida", "EXPIRED": "Vencida",
}
EVIDENCE_STATES = {
    "CREATED": "Creada", "HASHED": "Generada, sello pendiente", "SUBMITTED": "Sello enviado",
    "PENDING_BITCOIN": "Esperando Bitcoin", "CONFIRMED": "Anclada en Bitcoin", "VERIFIED": "Verificada",
    "FAILED": "Fallida",
}
EVENT_LABELS = {
    "EVIDENCE_CREATED": "Evidencia creada", "PAYMENT_CONFIRMED": "Pago confirmado", "FILE_ADDED": "Archivo agregado",
    "MANIFEST_HASHED": "Huella generada", "PROOF_SAVED": "Prueba guardada", "PROOF_UPDATED": "Prueba actualizada",
    "STATUS_CHANGED": "Cambio de estado", "OTS_SUBMISSION_FAILED": "Falló el envío del sello",
    "BITCOIN_VERIFICATION_FAILED": "Falló la verificación contra Bitcoin", "PROOF_MISSING": "Falta la prueba",
}

# Grouping for the dashboard: presentation only
PHASES = [
    ("Onboarding", ["esperando_email", "esperando_pago"]),
    ("Registro enviado", ["generando"]),
    ("Registro solicitado", ["por_sellar", "esperando_bitcoin"]),
    ("Registro aceptado", ["anclada", "verificada"]),
    ("Enviado al cliente", ["certificada", "entregada"]),
    ("Cierre", ["expirada", "fallida"]),
]
PROCESSES = [("registrador", "Registrador", "Genera y pide el sello a los calendarios"),
             ("verificador", "Verificador", "Pregunta si Bitcoin aceptó y verifica contra el bloque"),
             ("certificador", "Certificador", "Emite el certificado de PRIMA y el paquete de entrega"),
             ("control", "Control", "Vence lo que corresponde y avisa de fallas y atrasos"),
             ("avisos", "Avisos", "Envía los correos al cliente y a la administración")]

def ago(moment: Optional[datetime]) -> str:
    """'hace 3 min', 'hace 2 h', 'hace 4 d'."""
    if not moment:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    seconds = max(0, int((datetime.now(timezone.utc) - moment).total_seconds()))
    if seconds < 60:
        return "hace instantes"
    if seconds < 3600:
        return f"hace {seconds // 60} min"
    if seconds < 86400:
        return f"hace {seconds // 3600} h"
    return f"hace {seconds // 86400} d"

NOTICE_KINDS = {
    "cliente.confirmacion_email": "Confirmación de email",
    "cliente.recepcion_solicitud": "Recepción de solicitud", "cliente.recepcion_pago": "Recepción de pago",
    "cliente.confirmacion_registro": "Confirmación y registro", "admin.solicitud_nueva": "Solicitud nueva (administración)",
    "admin.falla": "Falla (administración)", "admin.etapa_atrasada": "Etapa atrasada (administración)",
}
NOTICE_STATES = {"pending": "Pendiente", "sent": "Enviado", "failed": "Fallido"}

def short_hash(value: Optional[str]) -> str:
    return f"{value[:12]}…{value[-6:]}" if value and len(value) > 20 else (value or "")

def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"

def register_admin(app: FastAPI, svc: Services, templates: Jinja2Templates, settings: Settings,
                   category_labels: dict, client_ip, login_limiter, bot_problem=None, bot_widget=None) -> None:
    serializer = URLSafeTimedSerializer(settings.session_secret, salt="prima-admin")
    max_age = settings.admin_session_hours * 3600
    secure_cookie = settings.env in ("production", "staging")

    templates.env.filters["short_hash"] = short_hash
    templates.env.filters["human_size"] = human_size
    templates.env.filters["ago"] = ago
    stale_after = 3 * settings.worker_interval_seconds

    def render(request: Request, name: str, status_code: int = 200, **ctx):
        ctx.setdefault("admin", None)
        ctx.update(request_states=REQUEST_STATES, evidence_states=EVIDENCE_STATES, event_labels=EVENT_LABELS,
                   category_labels=category_labels, stage_labels=STAGE_LABELS, notice_kinds=NOTICE_KINDS,
                   notice_states=NOTICE_STATES)
        return templates.TemplateResponse(request, name, ctx, status_code=status_code)

    def current_admin(request: Request):
        """Admin of the session, or None. It is looked up in the database again: if the role is removed, the session stops working."""
        cookie = request.cookies.get(COOKIE)
        if not cookie:
            return None
        try:
            data = serializer.loads(cookie, max_age=max_age)
        except (BadSignature, SignatureExpired):
            return None
        user = svc.users.get_admin(data.get("uid", ""))
        return (user, data["csrf"]) if user else None

    def to_login():
        return RedirectResponse("/admin/login", status_code=303)

    def guard(request: Request):
        session = current_admin(request)
        return session

    def not_found(request: Request, session):
        return render(request, "admin_error.html", status_code=404, admin=session[0], csrf=session[1],
                      title="No encontrado", message="Lo que buscás no existe.")

    @app.middleware("http")
    async def admin_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/admin"):
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    # Acceso ---------------------------------------------------------------------------

    @app.get("/admin/login")
    def login_page(request: Request):
        if current_admin(request):
            return RedirectResponse("/admin", status_code=303)
        return render(request, "admin_login.html", error=None, **(bot_widget() if bot_widget else {}))

    @app.post("/admin/login")
    def login(request: Request, email: str = Form(""), password: str = Form(""),
              hp_trap_field: str = Form(""), form_token: str = Form(""),
              turnstile_token: str = Form("", alias="cf-turnstile-response")):
        widget = bot_widget() if bot_widget else {}
        if not login_limiter.allow(client_ip(request)):
            return render(request, "admin_login.html", status_code=429, **widget,
                          error="Demasiados intentos. Probá de nuevo en una hora.")
        problem = bot_problem(request, hp_trap_field, form_token, turnstile_token) if bot_problem else None
        if problem:
            return render(request, "admin_login.html", status_code=400, error=problem, **widget)
        found = svc.users.get_admin_credentials(email.strip().lower())
        # A hash is always verified, whether or not the user exists, and the error message is the same
        ok = verify_password(password, found[1] if found else DUMMY_HASH)
        if not (found and ok):
            return render(request, "admin_login.html", status_code=401, error="Email o contraseña incorrectos.", **widget)
        token = serializer.dumps({"uid": found[0].id, "csrf": secrets.token_urlsafe(16)})
        response = RedirectResponse("/admin", status_code=303)
        response.set_cookie(COOKIE, token, max_age=max_age, httponly=True, samesite="strict", secure=secure_cookie, path="/admin")
        return response

    @app.post("/admin/logout")
    def logout(request: Request, csrf: str = Form("")):
        session = current_admin(request)
        response = RedirectResponse("/admin/login", status_code=303)
        if session and secrets.compare_digest(csrf, session[1]):
            response.delete_cookie(COOKIE, path="/admin")
        return response

    # Dashboard ------------------------------------------------------------------------

    @app.get("/admin")
    def dashboard(request: Request):
        session = guard(request)
        if not session:
            return to_login()
        q = svc.admin_queries
        rc, ec = q.request_counts(), q.evidence_counts()
        runs = q.last_runs()
        now = datetime.now(timezone.utc)
        processes = []
        for code, label, description in PROCESSES:
            if code == "avisos" and settings.notification_delivery != "builtin":
                continue  # with n8n, its workflow does the sending, not this process
            run = runs.get(code)
            if run is None:
                health = "sin_corridas"
            elif run["error"]:
                health = "error"
            elif (now - run["started_at"]).total_seconds() > stale_after:
                health = "sin_actividad"
            else:
                health = "ok"
            processes.append({"label": label, "description": description, "run": run, "health": health})
        return render(request, "admin_dashboard.html", admin=session[0], csrf=session[1], active="resumen",
                      request_counts=rc, evidence_counts=ec, total_requests=sum(rc.values()),
                      total_evidences=sum(ec.values()), total_users=q.total_users(),
                      stage_counts=q.stage_counts(), phases=PHASES, processes=processes,
                      notice_counts=svc.notifications.counts(), stale_notices=q.stale_pending_notices(10),
                      overdue=Counter(o["stage"] for o in overdue_operations(svc.engine, settings.stage_deadlines_minutes)),
                      stale_minutes=stale_after // 60,
                      latest_requests=q.search_requests(limit=8), latest_evidences=q.search_evidences(limit=8),
                      events=q.recent_events())

    @app.get("/admin/operaciones")
    def operations_list(request: Request, etapa: str = "", q: str = "", cliente: str = ""):
        session = guard(request)
        if not session:
            return to_login()
        stage = etapa if etapa in STAGE_LABELS else None
        overdue_ids = {str(o["request_id"]) for o in overdue_operations(svc.engine, settings.stage_deadlines_minutes)}
        return render(request, "admin_operations.html", admin=session[0], csrf=session[1], active="operaciones",
                      overdue_ids=overdue_ids,
                      rows=svc.admin_queries.search_operations(stage=stage, q=q.strip()[:100] or None,
                                                               client=cliente.strip()[:255] or None),
                      etapa=stage or "", q=q.strip()[:100], cliente=cliente.strip()[:255],
                      clients=svc.admin_queries.list_clients(), stages=STAGES)

    # Notices and manual actions ------------------------------------------------------------

    def csrf_ok(session, token: str) -> bool:
        return bool(token) and secrets.compare_digest(token, session[1])

    def forbidden(request: Request, session):
        return render(request, "admin_error.html", status_code=403, admin=session[0], csrf=session[1],
                      title="Acción no permitida", message="La sesión no coincide con el formulario. Volvé a intentarlo.")

    def refused(request: Request, session, message: str):
        return render(request, "admin_error.html", status_code=400, admin=session[0], csrf=session[1],
                      title="No se pudo hacer", message=message)

    @app.get("/admin/avisos")
    def notices_list(request: Request, estado: str = "", tipo: str = ""):
        session = guard(request)
        if not session:
            return to_login()
        status = estado if estado in NOTICE_STATES else None
        kind = tipo if tipo in NOTICE_KINDS else None
        return render(request, "admin_avisos.html", admin=session[0], csrf=session[1], active="avisos",
                      rows=svc.notifications.list(status=status, kind=kind), estado=status or "", tipo=kind or "",
                      counts=svc.notifications.counts())

    @app.post("/admin/avisos/{notification_id}/reenviar")
    def notice_requeue(request: Request, notification_id: str, csrf: str = Form("")):
        session = guard(request)
        if not session:
            return to_login()
        if not csrf_ok(session, csrf):
            return forbidden(request, session)
        if not svc.actions.requeue_notice(notification_id):
            return refused(request, session, "Solo se pueden reenviar los avisos fallidos.")
        return RedirectResponse("/admin/avisos", status_code=303)

    @app.post("/admin/evidencias/{evidence_id}/reintentar-sellado")
    def retry_seal(request: Request, evidence_id: str, csrf: str = Form("")):
        session = guard(request)
        if not session:
            return to_login()
        if not EVIDENCE_ID_RE.match(evidence_id):
            return not_found(request, session)
        if not csrf_ok(session, csrf):
            return forbidden(request, session)
        try:
            svc.actions.retry_seal(evidence_id[:64], session[0].email)
        except KeyError:
            return not_found(request, session)
        except ValueError as e:
            return refused(request, session, str(e))
        return RedirectResponse(f"/admin/evidencias/{evidence_id}", status_code=303)

    @app.post("/admin/evidencias/{evidence_id}/reintentar-certificado")
    def retry_certificate(request: Request, evidence_id: str, csrf: str = Form("")):
        session = guard(request)
        if not session:
            return to_login()
        if not EVIDENCE_ID_RE.match(evidence_id):
            return not_found(request, session)
        if not csrf_ok(session, csrf):
            return forbidden(request, session)
        try:
            svc.actions.retry_certificate(evidence_id[:64], session[0].email)
        except KeyError:
            return not_found(request, session)
        except ValueError as e:
            return refused(request, session, str(e))
        return RedirectResponse(f"/admin/evidencias/{evidence_id}", status_code=303)

    @app.get("/admin/solicitudes")
    def requests_list(request: Request, estado: str = "", q: str = "", cliente: str = ""):
        session = guard(request)
        if not session:
            return to_login()
        status = estado if estado in REQUEST_STATES else None
        return render(request, "admin_requests.html", admin=session[0], csrf=session[1], active="solicitudes",
                      rows=svc.admin_queries.search_requests(status=status, q=q.strip()[:100] or None, client=cliente.strip()[:255] or None),
                      estado=status or "", q=q.strip()[:100], cliente=cliente.strip()[:255],
                      clients=svc.admin_queries.list_clients())

    @app.get("/admin/solicitudes/{request_id}")
    def request_detail(request: Request, request_id: str):
        session = guard(request)
        if not session:
            return to_login()
        try:
            uuid.UUID(request_id)
        except ValueError:
            return not_found(request, session)
        detail = svc.admin_queries.request_detail(request_id)
        if not detail:
            return not_found(request, session)
        return render(request, "admin_request_detail.html", admin=session[0], csrf=session[1], active="solicitudes", r=detail,
                      notices=svc.notifications.list(request_id=request_id))

    @app.get("/admin/evidencias")
    def evidences_list(request: Request, estado: str = "", categoria: str = "", q: str = "", cliente: str = ""):
        session = guard(request)
        if not session:
            return to_login()
        status = estado if estado in EVIDENCE_STATES else None
        category = categoria if categoria in category_labels else None
        return render(request, "admin_evidences.html", admin=session[0], csrf=session[1], active="evidencias",
                      rows=svc.admin_queries.search_evidences(status=status, category=category, q=q.strip()[:100] or None,
                                                              client=cliente.strip()[:255] or None),
                      estado=status or "", categoria=category or "", q=q.strip()[:100], cliente=cliente.strip()[:255],
                      clients=svc.admin_queries.list_clients(),
                      categories=sorted(category_labels.items(), key=lambda kv: kv[1]))

    def certificate_file(request: Request, evidence_id: str, kind: str):
        session = guard(request)
        if not session:
            return to_login()
        cert = svc.certifier.get_certificate(evidence_id[:64])
        if not cert:
            return not_found(request, session)
        ref = cert["pdf_ref"] if kind == "pdf" else cert["package_ref"]
        root = os.path.realpath(svc.storage.storage_root)
        path = os.path.realpath(os.path.join(root, ref))
        if not path.startswith(root + os.sep) or not os.path.isfile(path):
            return not_found(request, session)
        with open(path, "rb") as f:
            content = f.read()
        if kind == "pdf":
            return Response(content, media_type="application/pdf",
                            headers={"Content-Disposition": f'inline; filename="certificado-{evidence_id}.pdf"'})
        return Response(content, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="paquete-{evidence_id}.zip"'})

    @app.get("/admin/evidencias/{evidence_id}/certificado.pdf")
    def certificate_pdf(request: Request, evidence_id: str):
        return certificate_file(request, evidence_id, "pdf")

    @app.get("/admin/evidencias/{evidence_id}/paquete.zip")
    def certificate_package(request: Request, evidence_id: str):
        return certificate_file(request, evidence_id, "zip")

    @app.get("/admin/evidencias/{evidence_id}")
    def evidence_detail(request: Request, evidence_id: str):
        session = guard(request)
        if not session:
            return to_login()
        detail = svc.admin_queries.evidence_detail(evidence_id[:64])
        if not detail:
            return not_found(request, session)
        manifest = None
        if detail["proof"]:
            try:
                manifest = json.dumps(json.loads(detail["proof"]["manifest_json"]), indent=2, ensure_ascii=False)
            except ValueError:
                manifest = detail["proof"]["manifest_json"]
        proof = detail["proof"]
        return render(request, "admin_evidence_detail.html", admin=session[0], csrf=session[1], active="evidencias",
                      e=detail, manifest=manifest,
                      can_retry_seal=detail["status"] == "FAILED" and bool(proof) and not proof["has_seal"],
                      can_retry_certificate=detail["status"] == "VERIFIED" and not detail["certificate"])
