import re
from typing import Dict, Literal
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_STORAGE_SECRET = "dev-only-storage-secret"
DEV_SESSION_SECRET = "dev-only-session-secret"

class Settings(BaseSettings):
    """System settings. Environment variables with the PROOF_ prefix or a .env file (gitignored)."""
    model_config = SettingsConfigDict(env_prefix="PROOF_", env_file=".env", extra="ignore")

    env: Literal["development", "staging", "production"] = "development"
    database_url: str = "postgresql+psycopg://prima:prima_dev@127.0.0.1:55432/prima_dev"
    storage_root: str = "storage"
    storage_signing_secret: str = DEV_STORAGE_SECRET

    # Payment: "bypass" confirms without charging (internal tests with own projects); "stripe" charges for real (task 07).
    payment_mode: Literal["bypass", "stripe"] = "bypass"
    price_usd: float = 0.0

    # Prefix of each evidence id: PRIMA-20261004-A1B2C3D4. Changing it does not affect evidences already created.
    evidence_id_prefix: str = "PRIMA"

    # Timestamp retries before marking an evidence as FAILED
    ots_max_retries: int = 3
    worker_interval_seconds: int = 60

    # PRIMA certificate
    package_include_originals: bool = True
    # The server does not keep client files (only fingerprints and records). Storing them again is for development only.
    store_originals: bool = False
    certificate_max_attempts: int = 3
    certificate_layout_path: str = ""  # empty = template included in src/certificate_templates
    chromium_no_sandbox: bool = False  # True only inside a container, where the Chromium sandbox does not start

    # Notifications: "builtin" sends them through the local development consumer (writes to the outbox folder); "external" lets n8n consume them through the internal API
    notification_delivery: Literal["builtin", "external"] = "builtin"
    internal_api_token: str = ""  # empty = internal API disabled
    mcp_enabled: bool = False  # off = the whole MCP entry answers 404. Turned on at the operator's request
    mcp_rate_per_hour: int = 60  # registrations per hour for each MCP key
    mcp_declaration_receipt_minutes: int = 60  # how long the receipt of having requested the declaration is valid for registering
    notification_max_attempts: int = 10
    notification_wake_url: str = ""  # n8n webhook called when a notification is queued (uses internal_api_token); empty = n8n's clock only

    # Deadlines per stage, in minutes (proposed by the operator, to be confirmed). A stage without a deadline is not watched.
    stage_deadlines_minutes: Dict[str, int] = {
        "esperando_pago": 1440, "generando": 5, "por_sellar": 15, "esperando_bitcoin": 1440,
        "anclada": 30, "verificada": 30, "certificada": 30,
    }

    # Admin dashboard
    session_secret: str = DEV_SESSION_SECRET
    admin_session_hours: int = 8
    login_rate_per_hour: int = 10

    # Behind a reverse proxy (e.g. Nginx with Cloudflare) the visitor's IP comes in a header. It is trusted only if the
    # connection comes from one of the trusted IPs (comma separated); otherwise the connection IP is used.
    proxy_ip_header: str = ""
    trusted_proxy_ips: str = ""

    # Bot protection. Turnstile (Cloudflare) turns on with both keys; the guard adds a honeypot field and a minimum time.
    # Both are mandatory in staging and production.
    turnstile_site_key: str = ""
    turnstile_secret_key: str = ""
    form_guard_enabled: bool = False
    form_min_seconds: int = 3
    submit_rate_per_email_hour: int = 3  # requests per hour to the same address (prevents using the portal to email a third party)
    submit_global_per_hour: int = 60  # total cap of requests per hour, wherever they come from
    resend_rate_per_request_hour: int = 3
    expired_files_retention_hours: int = 168  # files of an expired request are deleted after this time, and it can no longer be revived

    # Portal web
    host: str = "127.0.0.1"
    port: int = 8050
    public_base_url: str = "http://127.0.0.1:8050"
    verification_ttl_hours: int = 48  # internal to the request: the link the client sees is email_link_ttl_hours
    email_link_ttl_hours: int = 24  # validity of the emailed link that opens the form
    max_files_per_request: int = 10
    max_file_mb: int = 50
    max_request_mb: int = 50  # total size cap per request, adding up all files (decided 2026-10-12)
    max_video_mb: int = 25  # free service: 480p video (the client reduces it before registering); raised with the MCP and the subscription
    temp_budget_mb: int = 5120  # maximum space for files in transit; when exceeded, the client is asked to wait a few minutes (not an error)
    submit_rate_per_hour: int = 10
    verify_rate_per_hour: int = 30

    # Mail: the local development consumer writes emails to a folder instead of sending them
    outbox_dir: str = "outbox"
    admin_email: str = ""

    @model_validator(mode="after")
    def _production_guards(self):
        if not re.fullmatch(r"[A-Z0-9]{2,8}", self.evidence_id_prefix):
            raise ValueError("The id prefix must have 2 to 8 uppercase letters or digits.")
        if self.env == "production" and self.payment_mode == "bypass":
            raise ValueError("In production the payment cannot be in bypass mode.")
        # staging: real server with internal tests (bypass allowed), but with every other production requirement
        if self.env in ("production", "staging"):
            if self.storage_signing_secret == DEV_STORAGE_SECRET:
                raise ValueError("In production PROOF_STORAGE_SIGNING_SECRET must be set.")
            if self.session_secret == DEV_SESSION_SECRET:
                raise ValueError("In production PROOF_SESSION_SECRET must be set.")
            if self.notification_delivery != "external":
                raise ValueError("In production n8n consumes the notifications: use PROOF_NOTIFICATION_DELIVERY=external.")
            if len(self.internal_api_token) < 32:
                raise ValueError("In production PROOF_INTERNAL_API_TOKEN must be set (at least 32 characters).")
            if not (self.turnstile_site_key and self.turnstile_secret_key):
                raise ValueError("In production PROOF_TURNSTILE_SITE_KEY and PROOF_TURNSTILE_SECRET_KEY must be set.")
            if not self.form_guard_enabled:
                raise ValueError("In production PROOF_FORM_GUARD_ENABLED must be on.")
        return self

def get_settings() -> Settings:
    return Settings()
