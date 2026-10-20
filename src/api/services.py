from dataclasses import dataclass
from typing import Callable, Optional
from sqlalchemy.engine import Engine
from src.core.admin_actions import AdminActions
from src.core.admin_queries import AdminQueries
from src.core.audit_logger import AuditLoggerManager
from src.core.certificate import CertificateIssuer, ChromiumPdfRenderer, PdfRenderer
from src.core.config import Settings
from src.core.control import ControlProcess
from src.core.creation_flow import EvidenceCreationPipeline
from src.core.db import make_engine
from src.core.email_verifications import EmailVerificationManager
from src.core.evidence_manager import EvidenceManager
from src.core.evidence_requests import RequestManager
from src.core.legal import LegalVersionManager
from src.core.mcp_keys import McpKeyManager
from src.core.mailer import Mailer, build_mailer
from src.core.notification_delivery import BuiltinDelivery
from src.core.notification_events import NotificationEvents
from src.core.notification_wake import webhook_wake
from src.core.notifications import NotificationManager
from src.core.ots import submit_hash_to_opentimestamps
from src.core.ots_db_worker import DbOTSWorker
from src.core.ots_worker import OTSWorkerService
from src.core.payments import build_payment_gate
from src.core.request_service import RequestService
from src.core.storage import PrivateStorageManager
from src.core.users import UserManager

@dataclass
class Services:
    settings: Settings
    engine: Engine
    users: UserManager
    audit: AuditLoggerManager
    evidence: EvidenceManager
    legal: LegalVersionManager
    requests: RequestManager
    mailer: Mailer
    storage: PrivateStorageManager
    pipeline: EvidenceCreationPipeline
    request_service: RequestService
    worker: DbOTSWorker
    admin_queries: AdminQueries
    certifier: CertificateIssuer
    notifications: NotificationManager
    events: NotificationEvents
    delivery: Optional[BuiltinDelivery]
    actions: AdminActions
    mcp_keys: McpKeyManager

def build_services(settings: Settings, engine: Optional[Engine] = None, mailer: Optional[Mailer] = None,
                   submit_fn: Callable[[str], bytes] = submit_hash_to_opentimestamps,
                   renderer: Optional[PdfRenderer] = None) -> Services:
    """Builds every service from the settings. `engine`, `mailer` and `submit_fn` can be injected (tests)."""
    engine = engine or make_engine(settings.database_url)
    audit = AuditLoggerManager(engine)
    evidence = EvidenceManager(engine, audit)
    users = UserManager(engine)
    legal = LegalVersionManager(engine)
    storage = PrivateStorageManager(settings.storage_root, settings.storage_signing_secret)
    mailer = mailer or build_mailer(settings)
    pipeline = EvidenceCreationPipeline(
        storage_manager=storage, evidence_manager=evidence,
        ots_worker=OTSWorkerService(max_retries=settings.ots_max_retries, submit_fn=submit_fn),
        legal_versions=legal, payment_gate=build_payment_gate(settings), price_usd=settings.price_usd,
        id_prefix=settings.evidence_id_prefix, store_originals=settings.store_originals)
    requests = RequestManager(engine, settings.storage_root, settings.session_secret)
    notifications = NotificationManager(engine, settings.notification_max_attempts,
                                        wake=webhook_wake(settings.notification_wake_url, settings.internal_api_token))
    events = NotificationEvents(notifications, settings, requests, engine)
    certifier = CertificateIssuer(
        engine, storage, legal, audit, renderer or ChromiumPdfRenderer(no_sandbox=settings.chromium_no_sandbox),
        include_originals=settings.package_include_originals,
        layout_path=settings.certificate_layout_path or None)
    delivery = BuiltinDelivery(notifications, mailer, certifier) if settings.notification_delivery == "builtin" else None
    request_service = RequestService(settings, requests, users, pipeline, legal, events, EmailVerificationManager(engine))
    return Services(
        settings=settings, engine=engine, users=users, audit=audit, evidence=evidence, legal=legal,
        requests=requests, mailer=mailer, storage=storage, pipeline=pipeline,
        request_service=request_service,
        actions=AdminActions(engine, evidence, notifications, request_service),
        worker=DbOTSWorker(engine, evidence, max_retries=settings.ots_max_retries, submit_fn=submit_fn,
                           certifier=certifier, certify_max_attempts=settings.certificate_max_attempts,
                           events=events, delivery=delivery,
                           control=ControlProcess(engine, events, settings.stage_deadlines_minutes,
                                                  staging_root=requests.staging_root,
                                                  purge_after_hours=settings.expired_files_retention_hours)),
        admin_queries=AdminQueries(engine), certifier=certifier, notifications=notifications, events=events,
        delivery=delivery, mcp_keys=McpKeyManager(engine))
