from enum import Enum
from typing import Set, Dict

class EvidenceState(str, Enum):
    CREATED = "CREATED"
    HASHED = "HASHED"
    SUBMITTED = "SUBMITTED"
    PENDING_BITCOIN = "PENDING_BITCOIN"
    CONFIRMED = "CONFIRMED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"

# Map of allowed state transitions
ALLOWED_TRANSITIONS: Dict[EvidenceState, Set[EvidenceState]] = {
    EvidenceState.CREATED: {EvidenceState.HASHED, EvidenceState.FAILED},
    EvidenceState.HASHED: {EvidenceState.SUBMITTED, EvidenceState.FAILED},
    EvidenceState.SUBMITTED: {EvidenceState.PENDING_BITCOIN, EvidenceState.CONFIRMED, EvidenceState.FAILED},
    EvidenceState.PENDING_BITCOIN: {EvidenceState.CONFIRMED, EvidenceState.FAILED},
    EvidenceState.CONFIRMED: {EvidenceState.VERIFIED, EvidenceState.FAILED},
    EvidenceState.VERIFIED: set(), # Estado final exitoso
    EvidenceState.FAILED: {EvidenceState.SUBMITTED, EvidenceState.HASHED} # Retries: the worker or the admin
}

def can_transition(current: EvidenceState, target: EvidenceState) -> bool:
    """Checks whether the transition between the current and the target status is valid."""
    if current == target:
        return True # Idempotent transition (same status)
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    return target in allowed

def validate_transition(current: EvidenceState, target: EvidenceState):
    """Raises ValueError if the status transition is not allowed."""
    if not can_transition(current, target):
        raise ValueError(f"Transición de estado no permitida: {current.value} -> {target.value}")
