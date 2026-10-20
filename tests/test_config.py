import unittest
from pydantic import ValidationError
from src.core.config import Settings
from src.core.payments import build_payment_gate, BypassPaymentGate, StripePaymentGate

class TestConfig(unittest.TestCase):

    def test_default_is_development_with_bypass_payment(self):
        s = Settings(_env_file=None)
        self.assertEqual((s.env, s.payment_mode), ("development", "bypass"))
        self.assertIsInstance(build_payment_gate(s), BypassPaymentGate)

    def test_evidence_id_prefix_is_validated(self):
        self.assertEqual(Settings(_env_file=None).evidence_id_prefix, "PRIMA")
        self.assertEqual(Settings(_env_file=None, evidence_id_prefix="FRAGOLA").evidence_id_prefix, "FRAGOLA")
        for bad in ("p", "prima", "CON ESPACIO", "DEMASIADOLARGO"):
            with self.assertRaises(ValidationError):
                Settings(_env_file=None, evidence_id_prefix=bad)

    def test_production_refuses_bypass_payment(self):
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, env="production", payment_mode="bypass", storage_signing_secret="x" * 32)

    def test_production_refuses_dev_signing_secret(self):
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, env="production", payment_mode="stripe")

    def test_production_refuses_the_local_notification_consumer(self):
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, env="production", payment_mode="stripe", storage_signing_secret="x" * 32,
                     session_secret="y" * 32, notification_delivery="builtin", internal_api_token="z" * 32)

    def test_production_requires_a_long_internal_api_token(self):
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, env="production", payment_mode="stripe", storage_signing_secret="x" * 32,
                     session_secret="y" * 32, notification_delivery="external", internal_api_token="corto")

    BOT_PROTECTION = dict(turnstile_site_key="site-key", turnstile_secret_key="secret-key", form_guard_enabled=True)

    def test_production_with_stripe_and_secret_is_valid(self):
        s = Settings(_env_file=None, env="production", payment_mode="stripe", storage_signing_secret="x" * 32, session_secret="y" * 32,
                     notification_delivery="external", internal_api_token="z" * 32, **self.BOT_PROTECTION)
        self.assertIsInstance(build_payment_gate(s), StripePaymentGate)

    def test_staging_allows_bypass_with_every_other_production_requirement(self):
        s = Settings(_env_file=None, env="staging", payment_mode="bypass", storage_signing_secret="x" * 32, session_secret="y" * 32,
                     notification_delivery="external", internal_api_token="z" * 32, **self.BOT_PROTECTION)
        self.assertEqual(s.payment_mode, "bypass")

    def test_staging_and_production_require_turnstile_and_the_form_guard(self):
        base = dict(_env_file=None, payment_mode="bypass", storage_signing_secret="x" * 32, session_secret="y" * 32,
                    notification_delivery="external", internal_api_token="z" * 32)
        with self.assertRaises(ValidationError):
            Settings(env="staging", **base)
        with self.assertRaises(ValidationError):
            Settings(env="staging", turnstile_site_key="site-key", form_guard_enabled=True, **base)
        with self.assertRaises(ValidationError):
            Settings(env="staging", turnstile_site_key="site-key", turnstile_secret_key="secret-key", **base)
        with self.assertRaises(ValidationError):
            Settings(env="production", **dict(base, payment_mode="stripe"))

    def test_staging_still_refuses_dev_secrets_and_the_local_consumer(self):
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, env="staging", payment_mode="bypass")
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, env="staging", payment_mode="bypass", storage_signing_secret="x" * 32,
                     session_secret="y" * 32, notification_delivery="builtin", internal_api_token="z" * 32)

if __name__ == "__main__":
    unittest.main()
