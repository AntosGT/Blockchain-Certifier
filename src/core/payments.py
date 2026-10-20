from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from src.core.config import Settings
from src.models.entities import UserSchema

@dataclass(frozen=True)
class PaymentResult:
    confirmed: bool
    method: str
    reference: Optional[str]
    amount_usd: float

class PaymentGate(ABC):
    """Validates the payment of an evidence (step 6). Chosen in the settings, not in code."""

    @abstractmethod
    def verify(self, user: UserSchema, amount_usd: float, payment_reference: Optional[str] = None) -> PaymentResult:
        ...

class BypassPaymentGate(PaymentGate):
    """Confirms without charging. For testing with own projects; the audit log records `method: bypass`."""

    def verify(self, user: UserSchema, amount_usd: float, payment_reference: Optional[str] = None) -> PaymentResult:
        return PaymentResult(confirmed=True, method="bypass", reference=payment_reference, amount_usd=amount_usd)

class StripePaymentGate(PaymentGate):
    """Verification against Stripe: implemented in task 07."""

    def verify(self, user: UserSchema, amount_usd: float, payment_reference: Optional[str] = None) -> PaymentResult:
        raise NotImplementedError("Stripe charging is implemented in task 07; use PROOF_PAYMENT_MODE=bypass until then.")

def build_payment_gate(settings: Settings) -> PaymentGate:
    return BypassPaymentGate() if settings.payment_mode == "bypass" else StripePaymentGate()
