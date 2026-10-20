import secrets
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import requests
from itsdangerous import BadSignature, URLSafeTimedSerializer

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

class TurnstileVerifier:
    """
    Validates with Cloudflare the Turnstile challenge solved by the visitor. Fails closed: if Cloudflare does not answer or the
    answer is unclear, the request is rejected (it is never let through by mistake).
    """

    def __init__(self, secret_key: str, post: Optional[Callable] = None, timeout: int = 5):
        self.secret_key = secret_key
        self.post = post or requests.post
        self.timeout = timeout

    def verify(self, token: str, remote_ip: Optional[str] = None) -> bool:
        if not token or len(token) > 2048:
            return False
        data = {"secret": self.secret_key, "response": token}
        if remote_ip and remote_ip != "unknown":
            data["remoteip"] = remote_ip
        try:
            response = self.post(SITEVERIFY_URL, data=data, timeout=self.timeout)
            return response.json().get("success") is True
        except Exception:
            return False

class FormGuard:
    """
    Cheap defenses against simple bots on the forms, without depending on a third party:
    - honeypot field: humans do not see it, bots fill it in;
    - signed timestamp: a submission that is too fast, or with an expired or tampered form, is rejected.
    """

    def __init__(self, secret: str, min_seconds: int = 3, max_age_seconds: int = 3600):
        self.serializer = URLSafeTimedSerializer(secret, salt="prima-form")
        self.min_seconds = min_seconds
        self.max_age_seconds = max_age_seconds

    def issue(self) -> str:
        return self.serializer.dumps(secrets.token_hex(8))

    def check(self, token: str, honeypot: str) -> bool:
        if honeypot:
            return False
        try:
            _, issued_at = self.serializer.loads(token or "", max_age=self.max_age_seconds, return_timestamp=True)
        except BadSignature:
            return False
        age = (datetime.now(timezone.utc) - issued_at).total_seconds()
        return age >= self.min_seconds
