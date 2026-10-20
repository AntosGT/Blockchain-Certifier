import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, EmailStr
from src.core.state_machine import EvidenceState

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class UserRole(str):
    USER = "user"
    ADMIN = "admin"

class UserSchema(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    email: str
    email_verified: bool = False
    name: str
    country: str = "AR"
    role: str = UserRole.USER
    document_id: Optional[str] = None
    identity_level: str = "declared"
    created_at: datetime = Field(default_factory=utc_now)

class EvidenceFileSchema(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    evidence_id: str
    filename: str
    mime: str
    size: int
    sha256: str
    salt_hex: str
    storage_reference: str
    created_at: datetime = Field(default_factory=utc_now)

class ProofSchema(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    evidence_id: str
    manifest_json: str
    manifest_hash: str
    ots_bytes: Optional[bytes] = None
    verification_status: str = "PENDING"
    bitcoin_block_height: Optional[int] = None
    bitcoin_tx_hash: Optional[str] = None
    verified_at: Optional[datetime] = None
    bitcoin_block_time: Optional[datetime] = None

class EvidenceRequestSchema(BaseModel):
    id: str
    email: str
    name: str
    country: str
    document_id: Optional[str] = None
    title: str
    category: str
    description: str = ""
    text_title: Optional[str] = None
    text_content: Optional[str] = None
    text_authors: Optional[str] = None
    declaration_version: str
    declaration_accepted_at: datetime
    status: str
    verification_expires_at: Optional[datetime] = None
    evidence_id: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    processed_at: Optional[datetime] = None
    lang: str = "es"

class AuditEventSchema(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    evidence_id: str
    event_type: str
    actor: str
    timestamp: datetime = Field(default_factory=utc_now)
    metadata: Dict[str, Any] = Field(default_factory=dict)

class EvidenceSchema(BaseModel):
    id: str
    user_id: str
    title: str
    category: str
    description: Optional[str] = ""
    status: EvidenceState = EvidenceState.CREATED
    privacy: str = "PRIVATE"
    manifest_hash: Optional[str] = None
    previous_evidence_id: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    confirmed_at: Optional[datetime] = None
    declaration_version: Optional[str] = None
    declaration_accepted_at: Optional[datetime] = None
