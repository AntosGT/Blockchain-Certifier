import hmac
import uuid
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from src.api.services import Services
from src.core.config import Settings

MAX_BATCH = 100

def register_internal(app: FastAPI, svc: Services, settings: Settings) -> None:
    """
    Internal API for the consumer of the notification queue (n8n). Protected by a token in the Authorization header.
    Without PROOF_INTERNAL_API_TOKEN configured the API does not exist (404), so it is never left open by mistake.
    """
    token = settings.internal_api_token.encode("utf-8")

    def denied(request: Request):
        """None if the request is authorized; otherwise, the rejection response."""
        if not token:
            return JSONResponse({"error": "not_found"}, status_code=404)
        header = request.headers.get("authorization", "")
        scheme, _, given = header.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(given.strip().encode("utf-8"), token):
            return JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        return None

    def valid_id(value: str) -> bool:
        try:
            uuid.UUID(value)
            return True
        except ValueError:
            return False

    def envelope(n: dict) -> dict:
        """Notification shape for n8n. `event_id` is the notification id: n8n uses it to never send the same one twice."""
        return {
            "event_id": n["id"], "kind": n["kind"], "channel": n["channel"], "attempts": n["attempts"],
            "created_at": n["created_at"].isoformat(),
            "to": {"name": n["recipient_name"], "email": n["recipient_email"]},
            "data": n["payload"],
            "attachment_url": f"/internal/avisos/{n['id']}/adjunto" if n["payload"].get("has_certificate") else None,
            "package_url": f"/internal/avisos/{n['id']}/paquete" if n["payload"].get("has_certificate") else None,
        }

    @app.get("/internal/salud")
    def internal_health(request: Request):
        return denied(request) or {"status": "ok"}

    @app.get("/internal/avisos/pendientes")
    def pending_notices(request: Request, limite: int = 20):
        rejected = denied(request)
        if rejected:
            return rejected
        batch = svc.notifications.lease_pending(max(1, min(limite, MAX_BATCH)))
        return {"count": len(batch), "items": [envelope(n) for n in batch]}

    @app.post("/internal/avisos/{notification_id}/enviado")
    def notice_sent(request: Request, notification_id: str):
        rejected = denied(request)
        if rejected:
            return rejected
        if not valid_id(notification_id) or not svc.notifications.mark_sent(notification_id):
            return JSONResponse({"error": "not_found"}, status_code=404)
        return {"ok": True}

    @app.post("/internal/avisos/{notification_id}/fallido")
    async def notice_failed(request: Request, notification_id: str):
        rejected = denied(request)
        if rejected:
            return rejected
        if not valid_id(notification_id) or not svc.notifications.get(notification_id):
            return JSONResponse({"error": "not_found"}, status_code=404)
        try:
            body = await request.json()
            error = str(body.get("error") or "Sin detalle")
        except Exception:
            error = "Sin detalle"
        status = svc.notifications.mark_failed(notification_id, error)
        if status is None:
            return JSONResponse({"error": "already_sent"}, status_code=409)
        return {"ok": True, "status": status}

    @app.get("/internal/avisos/{notification_id}/adjunto")
    def notice_attachment(request: Request, notification_id: str):
        rejected = denied(request)
        if rejected:
            return rejected
        notice = svc.notifications.get(notification_id) if valid_id(notification_id) else None
        if not notice or not notice["payload"].get("has_certificate") or not notice["evidence_id"]:
            return JSONResponse({"error": "not_found"}, status_code=404)
        pdf = svc.certifier.read_pdf(notice["evidence_id"])
        if pdf is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="certificate-{notice["evidence_id"]}.pdf"'})

    @app.get("/internal/avisos/{notification_id}/paquete")
    def notice_package(request: Request, notification_id: str):
        rejected = denied(request)
        if rejected:
            return rejected
        notice = svc.notifications.get(notification_id) if valid_id(notification_id) else None
        if not notice or not notice["payload"].get("has_certificate") or not notice["evidence_id"]:
            return JSONResponse({"error": "not_found"}, status_code=404)
        package = svc.certifier.read_package(notice["evidence_id"])
        if package is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return Response(package, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="verification-package-{notice["evidence_id"]}.zip"'})
