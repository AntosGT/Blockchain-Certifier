import os
import re
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from typing import List, Optional, Sequence, Tuple
from src.core.config import Settings

# (file name, content, MIME type)
Attachment = Tuple[str, bytes, str]

class Mailer(ABC):
    """
    Email sending for the local development consumer. In production n8n sends the email.
    """

    @abstractmethod
    def send(self, to: str, subject: str, text_body: str, attachments: Optional[Sequence[Attachment]] = None) -> None:
        ...

def _build_message(sender: str, to: str, subject: str, text_body: str,
                   attachments: Optional[Sequence[Attachment]]) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = sender
    msg["Subject"] = subject
    msg["Date"] = format_datetime(datetime.now(timezone.utc))
    msg.set_content(text_body, charset="utf-8")
    for filename, content, mime in attachments or []:
        maintype, _, subtype = mime.partition("/")
        msg.add_attachment(content, maintype=maintype, subtype=subtype or "octet-stream", filename=filename)
    return msg

class OutboxMailer(Mailer):
    """Development: saves each email as an .eml file in a folder and sends nothing out."""

    def __init__(self, outbox_dir: str):
        self.outbox_dir = os.path.abspath(outbox_dir)
        os.makedirs(self.outbox_dir, exist_ok=True)
        self.sent: List[EmailMessage] = []

    def send(self, to: str, subject: str, text_body: str, attachments: Optional[Sequence[Attachment]] = None) -> None:
        msg = _build_message("prima-certified <no-reply@localhost>", to, subject, text_body, attachments)
        safe_to = re.sub(r"[^A-Za-z0-9@._-]", "_", to)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = os.path.join(self.outbox_dir, f"{stamp}_{uuid.uuid4().hex[:6]}_{safe_to}.eml")
        with open(path, "wb") as f:
            f.write(bytes(msg))
        self.sent.append(msg)

def build_mailer(settings: Settings) -> Mailer:
    return OutboxMailer(settings.outbox_dir)
