"""Cross-role loops (spec section 6): does a message from one role ever get answered, and does the answer arrive?

Where the code has no reply channel yet, the tests assume a reply is stored as a decided case on the same record.
"""

import json
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from environments.norvale.integrations.company_run.test_behaviour_roles import Fake, finance
from environments.norvale.native import behaviour_roles as roles


def no_po_invoice():
    """Day-6 engine whose only invoice cites no purchase order."""
    engine = finance()
    for row in engine.api["FINANCE"].docs["Purchase Invoice", "PINV-1"]["items"]:
        row.pop("purchase_order")
    return engine


def short_two_days():
    engine = Fake(shortage_context={"RM-A": {"day": 6, "work_planned": 1}})
    engine.state["ops"]["shortage_days"] = {"RM-A": [5, 6]}
    return engine


@contextmanager
def quality(count):
    """count open NCs on item X, each on a rejected lot, served by a fake quality system (QMS)."""
    rows = [
        dict(name=f"NC-{i}", creation="2027-01-05", modified="m", status="Open", batch=f"B{i}", item="X")
        for i in range(count)
    ]
    store = {r["name"]: dict(r, custom_failed_inspection="I") for r in rows}
    qms = Mock()
    qms.get.side_effect = lambda doctype, name: dict(store.get(name, dict(name=name, workflow_state="Lot Rejected")))
    qms.update.side_effect = lambda doctype, name, **fields: store[name].update(fields)
    with (
        patch.object(roles, "nonconformances", side_effect=lambda engine: [dict(r) for r in store.values()]),
        patch.object(roles, "qa_actor", return_value=qms),
    ):
        yield store


def next_day(engine, days=1):
    engine.day += days
    roles.run(engine)


def cases_for(engine, role):
    return [c for c in engine.state["role_cases"].values() if c["role"].startswith(role)]


def reply(engine, role, record, text):
    engine.state.setdefault("role_cases", {})["reply"] = dict(
        key="reply",
        contract="reply",
        role=role,
        record=record,
        status="decided",
        action="reply",
        decided=engine.day,
        opened=engine.day,
        context={},
        reason=text,
        message=text,
    )


class InvoiceQuery(unittest.TestCase):
    """CFO <-> BUYER."""

    def test_question_reaches_the_buyer(self):
        """A non-PO invoice sends BUYER a mail. SOP: https://ramp.com/blog/accounts-payable/3-way-match"""
        engine = no_po_invoice()
        roles.run(engine)
        self.assertEqual([m[1:] for m in engine.mails], [("CFO", "BUYER")])

    # GAP issue #119 item 2: BUYER has no contract, so the question opens no case for the buyer.
    @unittest.expectedFailure
    def test_buyer_gets_a_case_to_answer(self):
        """The invoice query opens a BUYER case. SOP: none in the spec ([Judgment])."""
        engine = no_po_invoice()
        roles.run(engine)
        next_day(engine)
        self.assertTrue(cases_for(engine, "BUYER"))

    # GAP issue #119 items 1-2: invoice evidence has no inbox, so a reply is never seen.
    @unittest.expectedFailure
    def test_cfo_sees_the_buyers_answer(self):
        """The buyer's PO number appears in the CFO's next evidence. SOP: none in the spec ([Judgment])."""
        engine = no_po_invoice()
        reply(engine, "BUYER", "PINV-1", "PINV-1 belongs to PO-7788")
        evidence = roles.invoice_evidence(engine, dict(key="k", record="PINV-1", context={}))
        self.assertIn("PO-7788", json.dumps(evidence))


class ShortageEscalation(unittest.TestCase):
    """PLANNER <-> SC_MGR."""

    def test_escalation_is_assigned_to_supply_chain(self):
        """A repeated shortage creates a task for SC_MGR. SOP: none in the spec ([Judgment])."""
        engine = short_two_days()
        roles.run(engine)
        self.assertEqual([w[2]["allocated_to"] for w in engine.writes], ["scm_mgr@norvale.test"])

    # GAP issue #119 item 2: SC_MGR has no contract reading the escalation.
    @unittest.expectedFailure
    def test_supply_chain_gets_a_case(self):
        """The escalation opens an SC_MGR case. SOP: none in the spec ([Judgment])."""
        engine = short_two_days()
        roles.run(engine)
        next_day(engine)
        self.assertTrue(cases_for(engine, "SC"))

    # GAP issue #119 items 1-2: shortage evidence has no inbox, so an expected delivery date is never seen.
    @unittest.expectedFailure
    def test_planner_sees_the_delivery_date(self):
        """SC_MGR's delivery date appears in the planner's next evidence. SOP: none in the spec ([Judgment])."""
        engine = short_two_days()
        reply(engine, "SCM_MGR", "RM-A", "RM-A arrives 2027-01-19")
        evidence = roles.shortage_evidence(engine, dict(key="k", record="RM-A", context=dict(day=6, work_planned=1)))
        self.assertIn("2027-01-19", json.dumps(evidence))


class EngineeringReview(unittest.TestCase):
    """QA_MGR <-> PROC_ENG."""

    def open_review(self, engine, **over):
        engine.state["role_cases"] = {
            "d": dict(
                dict(
                    key="d",
                    contract="design_change",
                    role="PROC_ENG",
                    record="X",
                    status="open",
                    opened=engine.day,
                    due=engine.day + 5,
                    context={},
                ),
                **over,
            )
        }
        return engine.state["role_cases"]["d"]

    def qa_view(self, engine):
        return roles.qa_evidence(engine, dict(record="NC-0", context=dict(batch="B0", item="X")))

    def test_open_review_holds_the_nc_and_a_decision_releases_it(self):
        """QA keeps the NC open while engineering reviews, and not after. SOP: none in the spec ([Judgment])."""
        engine = Fake()
        with quality(1):
            review = self.open_review(engine, context=dict(item="X"))
            self.assertTrue(self.qa_view(engine)["engineering_review_open"])
            review.update(status="decided", action="no_change", decided=engine.day)
            self.assertFalse(self.qa_view(engine)["engineering_review_open"])

    # Regression for debsagar/enterpriseengine#120.
    def test_review_opened_by_the_real_trigger_holds_the_nc(self):
        """With two NCs and engineering undecided, QA does not close. SOP: none in the spec ([Judgment])."""
        engine = Fake()
        with quality(2):
            real_choose = engine.choose

            def choose(key, role, *args, **kwargs):
                if role == "PROC_ENG":
                    raise RuntimeError("engineering has not decided")
                return real_choose(key, role, *args, **kwargs)

            engine.choose = choose
            roles.run(engine)
        self.assertEqual({c["action"] for c in cases_for(engine, "QA_MGR")}, {"keep_open"})

    # Regression for debsagar/enterpriseengine#120.
    def test_overdue_review_still_holds_the_nc(self):
        """An overdue engineering review is still pending. SOP: none in the spec ([Judgment])."""
        engine = Fake()
        with quality(1):
            self.open_review(engine, context=dict(item="X"), status="escalated")
            self.assertTrue(self.qa_view(engine)["engineering_review_open"])

    # GAP issue #119 item 2: PROC_ENG's decision is not sent to QA_MGR.
    @unittest.expectedFailure
    def test_engineering_tells_quality_its_decision(self):
        """Deciding a design review notifies QA_MGR. SOP: none in the spec ([Judgment])."""
        engine = Fake()
        with quality(2):
            roles.run(engine)
        self.assertIn(("PROC_ENG", "QA_MGR"), [m[1:] for m in engine.mails])


class PlantEscalation(unittest.TestCase):
    """QA_MGR <-> PLANT_MGR."""

    def test_repeat_failures_reach_the_plant_manager(self):
        """Three NCs on one item mail PLANT_MGR. SOP: https://simplerqms.com/non-conformance/"""
        engine = Fake()
        with quality(3):
            roles.run(engine)
        self.assertIn(("QA_MGR", "PLANT_MGR"), [m[1:] for m in engine.mails])

    # GAP issue #119 item 2: PLANT_MGR has no contract to acknowledge or act.
    @unittest.expectedFailure
    def test_plant_manager_gets_a_case(self):
        """The escalation opens a PLANT_MGR case. SOP: https://simplerqms.com/non-conformance/"""
        engine = Fake()
        with quality(3):
            roles.run(engine)
            next_day(engine)
        self.assertTrue(cases_for(engine, "PLANT_MGR"))


class Overdue(unittest.TestCase):
    """Any role -> the role it escalates to."""

    # GAP issue #119 item 2, and docs/BEHAVIOUR.md ("escalated to this role as a new case"):
    # escalate_overdue (behaviour_roles.py:567-582) only sends a mail.
    @unittest.expectedFailure
    def test_overdue_case_opens_a_case_for_the_recipient(self):
        """An overdue invoice case opens a case for the CEO. SOP: none in the spec ([Judgment])."""
        engine = finance()
        engine.choose = Mock(side_effect=RuntimeError("no decision"))
        roles.run(engine)
        next_day(engine, 4)
        self.assertEqual([m[1:] for m in engine.mails], [("CFO", "CEO")])
        self.assertTrue(cases_for(engine, "CEO"))
