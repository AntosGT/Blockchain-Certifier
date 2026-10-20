import unittest
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import text
from db_helper import PostgresTestCase
from src.core.rate_limiter import RateLimiter

class TestRateLimiter(PostgresTestCase):

    def count(self):
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT count(*) FROM rate_limits")).scalar()

    def test_allows_up_to_the_limit_and_then_refuses_without_counting_the_refused(self):
        limiter = RateLimiter(self.engine, "submit", 2)
        self.assertEqual([limiter.allow("1.2.3.4") for _ in range(4)], [True, True, False, False])
        self.assertEqual(self.count(), 2)

    def test_the_limit_survives_a_restart_and_is_shared_between_processes(self):
        RateLimiter(self.engine, "submit", 1).allow("1.2.3.4")
        again = RateLimiter(self.engine, "submit", 1)  # another instance: a restart or a second process
        self.assertFalse(again.allow("1.2.3.4"))

    def test_keys_and_limit_names_do_not_step_on_each_other(self):
        submit, verify = RateLimiter(self.engine, "submit", 1), RateLimiter(self.engine, "verify", 1)
        self.assertTrue(submit.allow("1.2.3.4"))
        self.assertTrue(submit.allow("5.6.7.8"))
        self.assertTrue(verify.allow("1.2.3.4"))
        self.assertFalse(submit.allow("1.2.3.4"))

    def test_after_an_hour_it_allows_again_and_the_expired_hits_are_purged(self):
        limiter = RateLimiter(self.engine, "submit", 1)
        limiter.allow("1.2.3.4")
        limiter.allow("otra")
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE rate_limits SET hit_at = CURRENT_TIMESTAMP - interval '61 minutes'"))
        self.assertTrue(limiter.allow("1.2.3.4"))
        self.assertEqual(self.count(), 1)  # expired entries, even from another key, were deleted

    def test_the_stored_key_is_a_hash_not_the_ip_or_the_email(self):
        RateLimiter(self.engine, "email", 5).allow("ana@example.com")
        with self.engine.connect() as conn:
            stored = conn.execute(text("SELECT key_hash FROM rate_limits")).scalar()
        self.assertEqual(len(stored), 64)
        self.assertNotIn("ana", stored)

    def test_simultaneous_requests_do_not_pass_the_limit(self):
        limiter = RateLimiter(self.engine, "submit", 3)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: limiter.allow("1.2.3.4"), range(8)))
        self.assertEqual(results.count(True), 3)
        self.assertEqual(self.count(), 3)

if __name__ == "__main__":
    unittest.main()
