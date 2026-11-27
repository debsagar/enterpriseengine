"""PROC_ENG: whether repeated non-conformances (NCs, failed-lot records) on one item need a design change.

ECR = engineering change request. PLM = product lifecycle system that records those changes. BOM = bill of materials.
"""

import unittest
from unittest.mock import Mock, patch

from environments.norvale.integrations.company_run import definition_change
from environments.norvale.integrations.company_run.test_behaviour_roles import Fake
from environments.norvale.native import behaviour_roles as roles

THREE = ["NC-001", "NC-002", "NC-003"]


def decide(names, raised=False, **more):
    evidence = dict(nonconformances=names, change_raised_recently=raised, **more)
    evidence["facts"] = roles.design_facts(evidence, "2027-01-07")
    return (
        evidence["facts"]["count"],
        {f["flag"] for f in evidence["facts"]["flags"]},
        roles.design_rule(evidence)[0],
    )


def case(**over):
    return dict(key="design_change:FG-1", record="FG-1", context={}, **over)


def row(name, day):
    return dict(name=name, creation=Fake().dated(day), item="FG-1", batch="B" + name, status="Open", modified="m")


class ProcessEngineer(unittest.TestCase):
    def test_1_two_ncs_are_watched_not_changed(self):
        """Two NCs on an item in 14 days mean no change yet. SOP: none in the spec ([Judgment])."""
        self.assertEqual(decide(THREE[:2]), (2, set(), "no_change"))

    def test_2_three_ncs_request_a_change_without_editing_the_bom(self):
        """Three NCs raise a design change request and never write the BOM.
        SOP: https://kaizen.com/insights/engineering-change-management/
        SOP: https://tractian.com/en/glossary/engineering-change-request
        """
        self.assertEqual(decide(THREE), (3, set(), "raise_design_change"))
        engine = Fake()
        roles.design_raise(engine, case(), {}, dict(message="3 failures on FG-1"))
        self.assertTrue(engine.writes)
        self.assertNotIn("BOM", [w[1] for w in engine.writes])
        self.assertEqual(engine.api, {})

    def test_3_change_already_raised_is_not_raised_twice(self):
        """With a change raised inside the window, no second one is made.
        SOP: https://kaizen.com/insights/engineering-change-management/
        """
        self.assertEqual(decide(THREE, raised=True), (3, {"change_already_raised"}, "no_change"))
        engine = Fake()
        engine.state["role_cases"] = {
            "old": case(contract="design_change", status="decided", action="raise_design_change", decided=3)
        }
        with patch.object(roles, "nonconformances", return_value=[row(n, 4) for n in THREE]):
            self.assertTrue(roles.design_evidence(engine, case())["change_raised_recently"])

    def test_4_overdue_review_notifies_quality(self):
        """A design review opened day 1 and still open on day 7 is escalated to QA_MGR.
        SOP: none in the spec ([Judgment]).
        """
        engine = Fake()
        engine.day = 7
        engine.choose = Mock(side_effect=RuntimeError("no decision"))
        stale = case(contract="design_change", role="PROC_ENG", opened=1, due=6, status="open")
        engine.state["role_cases"] = {stale["key"]: stale}
        with patch.object(roles, "nonconformances", return_value=[]):
            roles.run(engine)
        self.assertEqual(stale["status"], "escalated")
        self.assertEqual(engine.mails, [("overdue:design_change:FG-1", "PROC_ENG", "QA_MGR")])

    def test_5_old_ncs_fall_outside_the_window(self):
        """Two NCs from day 1 and one from day 20 do not open a case on day 20. SOP: none in the spec ([Judgment])."""
        engine = Fake()
        engine.day = 20
        rows = [row("NC-001", 1), row("NC-002", 1), row("NC-003", 20)]
        with patch.object(roles, "nonconformances", return_value=rows):
            self.assertEqual(roles.design_trigger(engine), [])
            self.assertEqual(roles.design_evidence(engine, case())["nonconformances"], ["NC-003"])

    # GAP issue #119 item 5: design_raise writes a ToDo to the planner instead of a PLM change
    # (behaviour_roles.py:407-410).
    @unittest.expectedFailure
    def test_6_raising_a_change_creates_a_plm_record(self):
        """raise_design_change calls definition_change.propose.
        SOP: https://kaizen.com/insights/engineering-change-management/
        """
        engine = Fake()
        engine.plm = Mock()
        with patch.object(definition_change, "propose") as propose:
            roles.design_raise(engine, case(), dict(nonconformances=THREE), dict(message="3 failures on FG-1"))
        propose.assert_called_once()


class ProcessEngineerGaps(unittest.TestCase):
    """Spec "wrong or missing" items. The failure_modes field name is an assumption."""

    # GAP (spec section 5; issue #119 to-do): any 3 NCs count, with no grouping by failure mode.
    @unittest.expectedFailure
    def test_unrelated_failures_do_not_raise_a_change(self):
        """Three NCs with three different causes do not justify a design change.
        SOP: https://tractian.com/en/glossary/engineering-change-request
        """
        modes = dict(zip(THREE, ("viscosity", "label", "fill weight"), strict=True))
        self.assertEqual(decide(THREE, failure_modes=modes)[2], "no_change")
