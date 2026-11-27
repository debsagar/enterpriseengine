"""PLANNER: what to do when released raw material is short for planned work."""

import unittest
from unittest.mock import Mock

from environments.norvale.integrations.company_run.test_behaviour_roles import Fake
from environments.norvale.native import behaviour_roles as roles


def decide(days, work, **more):
    evidence = dict(shortage_days=days, work_planned_today=work, **more)
    evidence["facts"] = roles.shortage_facts(evidence, "2027-01-07")
    return (
        evidence["facts"]["streak"],
        {f["flag"] for f in evidence["facts"]["flags"]},
        roles.shortage_rule(evidence)[0],
    )


def case():
    return dict(key="shortage:RM-A", record="RM-A", context=dict(day=5, work_planned=2))


class Planner(unittest.TestCase):
    def test_1_first_day_short_with_other_work_reschedules(self):
        """On the first short day other orders are built instead.
        SOP: https://docs.infor.com/csi/2026.10/en-us/csbiolh/inventory_user_cl_sl/lsm1454144400736.html
        """
        self.assertEqual(decide([5], 2), (1, set(), "reschedule"))

    def test_2_first_day_short_with_nothing_else_waits(self):
        """With nothing else to build, a first-day shortage waits. SOP: none in the spec ([Judgment])."""
        self.assertEqual(decide([5], 0), (1, {"nothing_else_to_build"}, "wait"))

    def test_3_two_consecutive_days_escalate(self):
        """Two short days running escalate to supply chain, whether or not other work exists.
        SOP: https://help.sap.com/doc/9dadce5314894208e10000000a174cb4/3.6/en-US/0a98b6535fe6b74ce10000000a174cb4.html
        """
        for work in (0, 2):
            with self.subTest(work=work):
                streak, flags, action = decide([4, 5], work)
                self.assertEqual((streak, action), (2, "escalate"))
                self.assertIn("repeated_shortage", flags)
        engine = Fake()
        roles.shortage_escalate(engine, case(), {}, dict(message="RM-A short two days"))
        ((_, doctype, data, _),) = engine.writes
        self.assertEqual((doctype, data["allocated_to"]), ("ToDo", "scm_mgr@norvale.test"))

    def test_4_separate_short_days_are_not_a_streak(self):
        """Days 2 and 5 are two one-day gaps, not a repeat. SOP: none in the spec ([Judgment])."""
        self.assertEqual(decide([2, 5], 0), (1, {"nothing_else_to_build"}, "wait"))

    def test_5_overdue_shortage_notifies_supply_chain(self):
        """A shortage case still open past its one-day due date is escalated to SC_MGR.
        SOP: https://help.sap.com/doc/9dadce5314894208e10000000a174cb4/3.6/en-US/0a98b6535fe6b74ce10000000a174cb4.html
        """
        engine = Fake()
        engine.day = 7
        engine.choose = Mock(side_effect=RuntimeError("no decision"))
        stale = dict(case(), contract="shortage", role="PLANNER", opened=5, due=6, status="open")
        engine.state["role_cases"] = {stale["key"]: stale}
        roles.run(engine)
        self.assertEqual(stale["status"], "escalated")
        self.assertEqual(engine.mails, [("overdue:shortage:RM-A", "PLANNER", "SC_MGR")])

    # GAP (spec section 4 scenario 6; issue #119 to-do): lead time and open orders are ignored.
    @unittest.expectedFailure
    def test_6_shortage_inside_lead_time_escalates_on_day_one(self):
        """Stock needed in 3 days, 10 day lead time, nothing on order: escalate immediately.
        SOP: https://help.sap.com/doc/9dadce5314894208e10000000a174cb4/3.6/en-US/0a98b6535fe6b74ce10000000a174cb4.html
        """
        _, flags, action = decide([5], 2, released_qty=0, open_po_qty=0, need_days=3, lead_time_days=10)
        self.assertEqual(action, "escalate")
        self.assertIn("shortage_inside_lead_time", flags)


class PlannerGaps(unittest.TestCase):
    """Spec "wrong or missing" items."""

    # GAP (spec section 4; issue #119 to-do): shortage_evidence reads only Bin stock, no open purchase orders.
    @unittest.expectedFailure
    def test_evidence_includes_open_purchase_quantity(self):
        """The planner sees what is already on order for the short item.
        SOP: https://help.sap.com/doc/9dadce5314894208e10000000a174cb4/3.6/en-US/0a98b6535fe6b74ce10000000a174cb4.html
        """
        engine = Fake()
        engine.rows["Bin"] = [dict(item_code="RM-A", actual_qty=0)]
        engine.rows["Purchase Order"] = [dict(name="PO-1", item_code="RM-A", qty=40, received_qty=0)]
        self.assertIn("open_po_qty", roles.shortage_evidence(engine, case()))

    # GAP (spec section 4; issue #119 to-do): "reschedule" writes only a ToDo, no Work Order changes.
    @unittest.expectedFailure
    def test_reschedule_changes_a_work_order(self):
        """Rescheduling moves a Work Order, not just a task note. SOP: none in the spec ([Judgment])."""
        engine = Fake()
        engine.rows["Work Order"] = [dict(name="WO-1", production_item="FG-1", planned_start_date=engine.date())]
        roles.shortage_reschedule(engine, case(), {}, dict(message="Build another order"))
        self.assertIn("Work Order", [w[1] for w in engine.writes])
