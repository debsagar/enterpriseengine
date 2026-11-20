"""Offline service SLA regressions against Engine and the real consumer."""

from unittest.mock import patch

import test_engine_goals as fixtures


class ManagedServicesObligations(fixtures.Goals):
    def service(self, *, boundary=None, relief=None, grant=True):
        roles = {role: {} for role in ("worker", "manager", "relief", "peer")}
        if grant:
            roles["manager"]["permissions"] = ["obligation:reassign:worker"]
        if relief:
            roles["relief"] = relief
        config = {"roles": roles}
        return self.fixture("managed_services", boundary=boundary, config=config)

    def ticket(self, run, case="C", *, at=1, minutes=10):
        self.schedule(run, "contract", at - 1, key=f"contract-{case}",
                      case=case, response_minutes=minutes, rate=10)
        self.schedule(run, "ticket", at, key=f"ticket-{case}", role="worker",
                      case=case, ticket="T", technician="A")

    def test_eta_and_same_company_ticket_labels_keep_independent_deadlines(self):
        run, native, _, resume = self.service()
        self.ticket(run)
        self.ticket(run, "C2")
        run.run(until=1)
        first = self.one(native, "ticket", case="C", ticket="T")
        second = self.one(native, "ticket", case="C2", ticket="T")
        self.schedule(run, "eta", 4, role="worker", case="C", ticket="T", eta=15)
        # Even a response queued after the alarm can meet the deadline.
        self.schedule(run, "respond", 11, role="worker", case="C2", ticket="T")
        run.run(until=10)
        self.one(native, "ticket_eta", ticket_id=first["native_id"], eta=15)
        self.assertFalse(self.rows(native, "ticket_response"))
        self.assertFalse(self.rows(native, "sla_breach"))

        run = resume()
        run.run(until=20)
        self.one(native, "sla_breach", case="C", ticket_id=first["native_id"], at=11, due=11)
        response = self.one(native, "ticket_response", case="C2", ticket_id=second["native_id"], at=11)
        self.assertFalse(self.rows(native, "sla_breach", case="C2"))
        self.assertIn(("read", "tickets", response["native_id"]), native.calls)
        self.assertEqual(len([row for row in run.history()
                              if row["phase"] == "complete" and row["id"].startswith("obligation:")]), 1)

    def test_absent_assignee_transfers_and_late_response_replays(self):
        def crash(stage, operation):
            if stage == "result_persisted" and operation["action"]["kind"] in interruptions:
                interruptions.remove(operation["action"]["kind"])
                raise fixtures.PowerLoss

        interruptions = {"sla_breach", "ticket_response"}
        run, native, _, resume = self.service(boundary=crash, relief={"shifts": [[14, 22]]})
        self.ticket(run)
        self.schedule(run, "staff_unavailable", 2, case="C", technician="A")
        self.schedule(run, "respond", 3, key="blocked", role="worker", case="C", ticket="T")
        self.schedule(run, "reassign", 4, role="manager", case="C", ticket="T",
                      owner="relief", technician="B")
        self.schedule(run, "eta", 14, role="relief", case="C", ticket="T", eta=17)
        self.schedule(run, "respond", 17, role="relief", case="C", ticket="T")
        run.run(until=13)
        ticket = self.one(native, "ticket", case="C", ticket="T")
        self.one(native, "response_blocked", ticket_id=ticket["native_id"], technician="A")
        self.assertFalse(self.rows(native, "ticket_response"))
        self.assertFalse(self.rows(native, "sla_breach"))

        for _ in range(2):
            run = resume(crash)
            with self.assertRaises(fixtures.PowerLoss):
                run.run(until=17)
        run = resume(None)
        run.run(until=30)
        self.one(native, "ticket_eta", ticket_id=ticket["native_id"], technician="B", eta=17)
        self.one(native, "sla_breach", ticket_id=ticket["native_id"], due=11, at=14)
        response = self.one(native, "ticket_response", ticket_id=ticket["native_id"], technician="B", at=17)
        self.assertEqual(run.state["managed_services"]["responses"]["C"]["T"], response["native_id"])
        self.assertEqual(len([row for row in run.history()
                              if row["id"] == "respond" and row["phase"] == "complete"]), 1)

    def test_reassignment_checks_authority_absence_and_recipient(self):
        for role, absent, target, grant, error in [
            ("peer", True, "relief", True, PermissionError),
            ("manager", True, "relief", False, PermissionError),
            ("manager", False, "relief", True, ValueError),
            ("manager", True, "typo", True, ValueError),
        ]:
            with self.subTest(role=role, absent=absent, target=target, grant=grant):
                run, native, _, _ = self.service(grant=grant)
                self.ticket(run)
                if absent:
                    self.schedule(run, "staff_unavailable", 2, case="C", technician="A")
                self.schedule(run, "reassign", 3, role=role, case="C", ticket="T", owner=target)
                with self.assertRaises(error):
                    run.run(until=3)
                self.assertFalse(self.rows(native, "ticket_response"))

    def test_response_at_shift_reopening_still_breaches_contractual_due(self):
        run, native, _, _ = self.service(relief={"shifts": [[14, 22]]})
        self.ticket(run)
        self.schedule(run, "staff_unavailable", 2, case="C", technician="A")
        self.schedule(run, "reassign", 4, role="manager", case="C", ticket="T", owner="relief")
        run.run(until=12)
        self.assertFalse(self.rows(native, "sla_breach"))
        self.schedule(run, "respond", 13, role="relief", case="C", ticket="T")
        run.run(until=14)
        ticket = self.one(native, "ticket", case="C", ticket="T")
        self.one(native, "ticket_response", ticket_id=ticket["native_id"], at=14)
        self.one(native, "sla_breach", ticket_id=ticket["native_id"], at=14, due=11)

    def test_wrong_native_issue_id_cannot_close_the_matching_label(self):
        run, native, _, _ = self.service()
        self.ticket(run)
        self.ticket(run, "C2")
        run.run(until=1)
        other = self.one(native, "ticket", case="C2", ticket="T")
        self.schedule(run, "respond", 3, role="worker", case="C", ticket="T",
                      ticket_id=other["native_id"])
        with self.assertRaisesRegex(ValueError, "Native ticket ID"):
            run.run(until=3)
        self.assertFalse(self.rows(native, "ticket_response"))

    def test_mismatched_native_response_readback_does_not_commit_resolution(self):
        run, native, _, resume = self.service()
        self.ticket(run)
        self.ticket(run, "C2")
        run.run(until=1)
        other = self.one(native, "ticket", case="C2", ticket="T")
        self.schedule(run, "respond", 3, role="worker", case="C", ticket="T")
        read = native.read

        def wrong_issue(system, native_id):
            row = read(system, native_id)
            if row["kind"] == "ticket_response":
                row["ticket_id"] = other["native_id"]
            return row

        with patch.object(native, "read", side_effect=wrong_issue):
            with self.assertRaisesRegex(RuntimeError, "Native readback mismatch"):
                run.run(until=3)
        self.assertEqual(run.state["managed_services"]["responses"]["C"], {})
        run = resume()
        run.run(until=11)
        ticket = self.one(native, "ticket", case="C", ticket="T")
        self.one(native, "ticket_response", ticket_id=ticket["native_id"], at=3)
        self.assertFalse(self.rows(native, "sla_breach", case="C"))
        self.one(native, "sla_breach", ticket_id=other["native_id"], at=11)

    def test_role_free_ticket_preserves_queued_deadline_on_restart(self):
        run, native, _, resume = self.service()
        self.schedule(run, "contract", 0, case="C", response_minutes=10, rate=10)
        self.schedule(run, "ticket", 1, case="C", ticket="T")
        run.run(until=1)
        self.assertTrue(run.has_event('service-deadline:["C","T"]'))
        run = resume()
        run.run(until=11)
        ticket = self.one(native, "ticket", case="C", ticket="T")
        self.one(native, "sla_breach", ticket_id=ticket["native_id"], due=11, at=11)
