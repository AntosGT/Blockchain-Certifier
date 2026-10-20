import unittest
from sqlalchemy import text
from src.core import notifications as kinds
from src.core.control import ControlProcess, overdue_operations
from test_stages import StagesBase


class ControlBase(StagesBase):

    def setUp(self):
        super().setUp()
        self.settings = self.settings.model_copy(update={"admin_email": "admin@example.com"})
        from src.api.services import build_services
        from src.core.mailer import OutboxMailer
        self.svc = build_services(self.settings, engine=self.engine, mailer=OutboxMailer(self.settings.outbox_dir),
                                  submit_fn=lambda h: b"OTS-" + h.encode())
        self.control = ControlProcess(self.engine, self.svc.events, self.settings.stage_deadlines_minutes)

    def run_control(self, deadlines=None):
        if deadlines is not None:
            self.control.deadlines = deadlines
        stats = {"lifted": 0, "advanced": 0, "failed": 0, "deferred": 0}
        self.control.run({}, stats)
        return stats

    def admin_notices(self, kind):
        return [n for n in self.svc.notifications.list(kind=kind)]

    def backdate_request(self, rid, minutes):
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE evidence_requests SET updated_at = now() - make_interval(mins => :m) WHERE id = :i"),
                         {"m": minutes, "i": rid})


class TestExpiry(ControlBase):

    def test_expired_links_move_the_operation_to_expirada(self):
        expired, _ = self.make_operation("PENDING_EMAIL")
        alive, _ = self.make_operation("PENDING_EMAIL")
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE evidence_requests SET verification_expires_at = now() - interval '1 hour' WHERE id = :i"), {"i": expired})
        stats = self.run_control()
        self.assertEqual(stats["advanced"], 1)
        self.assertEqual(self.stage_of(expired), "expirada")
        self.assertEqual(self.stage_of(alive), "esperando_email")

    def test_expiry_does_not_raise_an_alert(self):
        rid, _ = self.make_operation("PENDING_EMAIL")
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE evidence_requests SET verification_expires_at = now() - interval '1 hour' WHERE id = :i"), {"i": rid})
        self.run_control()
        self.assertEqual(self.svc.notifications.list(), [])


class TestFailureAlerts(ControlBase):

    def test_a_failed_operation_alerts_the_admin_once_with_the_reason(self):
        rid, _ = self.make_operation("FAILED")
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE evidence_requests SET error = 'ValueError: algo falló' WHERE id = :i"), {"i": rid})
        first = self.run_control()
        self.assertEqual(first["failed"], 1)
        notices = self.admin_notices(kinds.FALLA)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["recipient_email"], "admin@example.com")
        self.assertEqual(notices[0]["payload"]["reason"], "ValueError: algo falló")
        self.assertEqual(notices[0]["payload"]["stage_label"], "Fallida")
        self.assertEqual(self.run_control()["failed"], 0)
        self.assertEqual(len(self.admin_notices(kinds.FALLA)), 1)

    def test_evidence_failures_explain_what_failed(self):
        _, eid = self.make_operation("PROCESSED", "FAILED")
        self.svc.audit.log_event(eid, "BITCOIN_VERIFICATION_FAILED", "system", {"error": "Digest does not match merkleroot"})
        self.run_control()
        reason = self.admin_notices(kinds.FALLA)[0]["payload"]["reason"]
        self.assertIn("alarma de integridad", reason)
        self.assertIn("Digest does not match merkleroot", reason)

    def test_a_new_failure_after_a_manual_retry_alerts_again(self):
        rid, _ = self.make_operation("FAILED")
        self.backdate_request(rid, 120)
        self.run_control()
        with self.engine.begin() as conn:  # the admin sent it back to its queue and it failed again later
            conn.execute(text("UPDATE evidence_requests SET status = 'PROCESSING', updated_at = now() - interval '60 minutes' WHERE id = :i"), {"i": rid})
            conn.execute(text("UPDATE evidence_requests SET status = 'FAILED', updated_at = now() WHERE id = :i"), {"i": rid})
        self.run_control()
        self.assertEqual(len(self.admin_notices(kinds.FALLA)), 2)

    def test_no_admin_address_means_no_admin_notices(self):
        self.svc.events.settings = self.svc.events.settings.model_copy(update={"admin_email": ""})
        self.make_operation("FAILED")
        self.assertEqual(self.run_control()["failed"], 0)
        self.assertEqual(self.svc.notifications.list(), [])


class TestOverdue(ControlBase):

    def test_an_operation_past_its_deadline_alerts_once(self):
        rid, _ = self.make_operation("PROCESSING")
        self.backdate_request(rid, 10)  # the "generando" deadline is 5 minutes
        stats = self.run_control()
        self.assertEqual(stats["deferred"], 1)
        notices = self.admin_notices(kinds.ETAPA_ATRASADA)
        self.assertEqual(len(notices), 1)
        payload = notices[0]["payload"]
        self.assertEqual((payload["stage"], payload["deadline_minutes"]), ("generando", 5))
        self.assertGreaterEqual(payload["minutes_in_stage"], 10)
        self.assertEqual(self.run_control()["deferred"], 0)
        self.assertEqual(len(self.admin_notices(kinds.ETAPA_ATRASADA)), 1)

    def test_within_the_deadline_nothing_happens(self):
        rid, _ = self.make_operation("PROCESSING")
        self.backdate_request(rid, 2)
        self.assertEqual(self.run_control()["deferred"], 0)
        self.assertEqual(self.admin_notices(kinds.ETAPA_ATRASADA), [])

    def test_stages_without_a_deadline_and_final_stages_are_never_overdue(self):
        waiting, _ = self.make_operation("PENDING_EMAIL")
        expired, _ = self.make_operation("EXPIRED")
        failed, _ = self.make_operation("FAILED")
        for rid in (waiting, expired, failed):
            self.backdate_request(rid, 100000)
        self.assertEqual(overdue_operations(self.engine, self.settings.stage_deadlines_minutes), [])

    def test_evidence_level_stage_uses_the_last_status_change(self):
        _, eid = self.make_operation("PROCESSED", "HASHED")
        self.assertEqual(overdue_operations(self.engine, {"por_sellar": 15}), [])
        late = overdue_operations(self.engine, {"por_sellar": 0})
        self.assertEqual([o["evidence_id"] for o in late], [eid])

    def test_each_stage_entry_is_a_separate_alert(self):
        _, eid = self.make_operation("PROCESSED", "HASHED")
        self.run_control({"por_sellar": 0})
        self.run_control({"por_sellar": 0})
        self.assertEqual(len(self.admin_notices(kinds.ETAPA_ATRASADA)), 1)

    def test_deadlines_are_configurable(self):
        rid, _ = self.make_operation("PROCESSING")
        self.backdate_request(rid, 10)
        self.run_control({"generando": 60})
        self.assertEqual(self.admin_notices(kinds.ETAPA_ATRASADA), [])
        self.run_control({"generando": 5})
        self.assertEqual(len(self.admin_notices(kinds.ETAPA_ATRASADA)), 1)


class TestControlInTheWorker(ControlBase):

    def test_the_worker_runs_control_and_records_it(self):
        rid, _ = self.make_operation("PROCESSING")
        self.backdate_request(rid, 10)
        self.svc.worker.run_cycle()
        with self.engine.connect() as conn:
            run = conn.execute(text("SELECT lifted, advanced, failed, deferred, error FROM worker_runs WHERE process = 'control'")).one()
        self.assertEqual((run.deferred, run.failed, run.error), (1, 0, None))
        self.assertGreaterEqual(run.lifted, 1)

    def test_the_alert_goes_out_in_the_same_cycle(self):
        rid, _ = self.make_operation("PROCESSING")
        self.backdate_request(rid, 10)
        self.svc.worker.run_cycle()
        self.assertEqual(self.admin_notices(kinds.ETAPA_ATRASADA)[0]["status"], "sent")


if __name__ == "__main__":
    unittest.main()
