import logging
import threading
from typing import Callable, Optional
import requests

logger = logging.getLogger("PROOF.Avisos")

def webhook_wake(url: str, token: str, timeout: float = 5.0) -> Optional[Callable[[], None]]:
    """
    n8n wake-up: when a notification is queued, the app calls the n8n webhook so it is sent right away instead of waiting for the clock.
    It runs in a separate thread and never fails outward: if n8n does not answer, the workflow's 2-minute clock picks up the notification anyway.
    Without a configured URL it returns None (no wake-up).
    """
    if not url:
        return None

    def call() -> None:
        try:
            requests.post(url, headers={"Authorization": f"Bearer {token}"}, json={}, timeout=timeout).raise_for_status()
        except Exception as e:
            logger.warning("Could not wake n8n (its clock picks it up anyway): %s", e)

    def wake() -> None:
        threading.Thread(target=call, daemon=True).start()

    return wake
