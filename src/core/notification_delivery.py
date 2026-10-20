import logging
from typing import Dict
from src.core.certificate import CertificateIssuer
from src.core.mailer import Mailer
from src.core.notification_templates import render_email
from src.core.notifications import NotificationManager

logger = logging.getLogger("PROOF.Avisos")

class BuiltinDelivery:
    """
    Local consumer of the notification queue, for development only (PROOF_NOTIFICATION_DELIVERY=builtin).
    It does the same as the n8n workflow: picks up pending notifications, sends them and confirms each delivery.
    In production n8n consumes them and this consumer does not run.
    """

    def __init__(self, notifications: NotificationManager, mailer: Mailer, certifier: CertificateIssuer):
        self.notifications = notifications
        self.mailer = mailer
        self.certifier = certifier

    def deliver_pending(self, stats: Dict[str, int], limit: int = 50) -> None:
        pending = self.notifications.lease_pending(limit)
        stats["lifted"] = len(pending)
        for n in pending:
            try:
                subject, body = render_email(n)
                attachments = []
                if n["payload"].get("has_certificate") and n["evidence_id"]:
                    pdf = self.certifier.read_pdf(n["evidence_id"])
                    if pdf is None:
                        raise RuntimeError("The certificate is not available to attach.")
                    attachments.append((f"certificate-{n['evidence_id']}.pdf", pdf, "application/pdf"))
                self.mailer.send(n["recipient_email"], subject, body, attachments)
            except Exception as e:
                logger.warning("Could not send notification %s: %s", n["id"], e)
                result = self.notifications.mark_failed(n["id"], f"{type(e).__name__}: {e}")
                stats["failed" if result == "failed" else "deferred"] += 1
                continue
            self.notifications.mark_sent(n["id"])
            stats["advanced"] += 1
