"""QA_MGR: non-conformances (NC = a record that a lot failed inspection)."""

import unittest
from unittest.mock import Mock, patch

from components.qms import ACTION, CASE

from environments.norvale.integrations.company_run.test_behaviour_roles import Fake
from environments.norvale.native import behaviour_roles as roles

QA = roles.BY_NAME["nonconformance"]


def nc(state="Lot Rejected", count=1, **over):
    base = dict(
        record=dict(doctype=CASE, name="NC-001"),
        inspection=dict(name="QI-1", workflow_state=state),
        batch="B1",
        item="FG-1",
        same_item_nonconformances=[f"NC-00{i}" for i in range(1, count + 1)],
        engineering_review_open=False,
        escalated_recently=False,
        # What a complete NC/CAPA record would carry; the code does not read these yet.
        root_cause="mixer fault",
        effectiveness_verified=True,
    )
    return {**base, **over}


def decide(evidence):
    facts = roles.qa_facts(evidence, "2027-01-07")
    action = roles.qa_rule({**evidence, "facts": facts})[0]
    return {f["flag"] for f in facts["flags"]}, action


class Quality(unittest.TestCase):
    def test_1_single_nc_on_rejected_lot_is_closed(self):
        """One NC on a rejected lot with no engineering review is closed. SOP: none in the spec ([Judgment])."""
        self.assertEqual(decide(nc()), (set(), "close"))

    def test_2_lot_still_in_retest_is_kept_open(self):
        """An NC whose lot has no final outcome stays open.
        SOP: https://www.epsilon3.io/behind-the-console/8-simple-steps-of-nonconformance-management
        """
        self.assertEqual(decide(nc("Pending Retest")), ({"lot_outcome_not_final"}, "keep_open"))

    def test_3_pending_engineering_review_keeps_nc_open(self):
        """An NC stays open while engineering is reviewing the item. SOP: none in the spec ([Judgment])."""
        self.assertEqual(decide(nc(engineering_review_open=True)), ({"engineering_review_pending"}, "keep_open"))

    def test_4_three_ncs_escalate_to_plant_manager(self):
        """Three NCs on one item inside 14 days are escalated to PLANT_MGR. SOP: https://simplerqms.com/non-conformance/"""
        self.assertEqual(decide(nc(count=3)), ({"repeat_failures_unescalated"}, "escalate"))
        engine = Fake()
        roles.qa_escalate(engine, dict(key="k", record="NC-001"), nc(count=3), dict(message="3 failures on NC-001"))
        self.assertEqual(engine.mails, [("escalation:k", "QA_MGR", "PLANT_MGR")])

    def test_5_already_escalated_item_is_not_escalated_again(self):
        """Three NCs already escalated in the window are closed, not re-escalated. SOP: none in the spec ([Judgment])."""
        self.assertEqual(decide(nc(count=3, escalated_recently=True)), (set(), "close"))

    def test_6_reworked_lot_is_not_closed(self):
        """An NC on a reworked lot is kept open, not closed (current half of scenario 6).
        SOP: https://www.epsilon3.io/behind-the-console/8-simple-steps-of-nonconformance-management
        """
        self.assertEqual(decide(nc("Lot Reworked"))[1], "keep_open")

    # GAP issue #119 item 5: nonconformances() reads only operating_lot_rejections, so reworked lots never open a case.
    @unittest.expectedFailure
    def test_6_reworked_lot_opens_a_case(self):
        """An open NC on a reworked lot triggers a QA case.
        SOP: https://www.epsilon3.io/behind-the-console/8-simple-steps-of-nonconformance-management
        """
        engine = Fake()
        engine.state["operating_lot_rejections"] = {}
        engine.company_erp = Mock()
        engine.company_erp.state = {
            "lots": {"B1": dict(item="FG-1")},
            "rework": {"rework:b1": dict(request=dict(batch="B1", inspection="QI-1"), output_batch="B1-R")},
        }
        qms = Mock()
        qms.list.return_value = [dict(name="NC-001", creation="2027-01-05", modified="m", status="Open")]
        with patch.object(roles, "qa_actor", return_value=qms):
            self.assertEqual([r["record"] for r in roles.qa_trigger(engine)], ["NC-001"])


class QualityGaps(unittest.TestCase):
    """Spec "wrong or missing" items. Evidence field names for the missing checks are assumptions."""

    # GAP (spec section 2; issue #119 to-do): lot rejection is only containment, yet qa_rule closes on it.
    @unittest.expectedFailure
    def test_nc_without_root_cause_or_effectiveness_check_is_not_closed(self):
        """An NC with no root cause and no effectiveness check stays open. SOP: https://sgsystemsglobal.com/?p=42727"""
        self.assertNotEqual(decide(nc(root_cause=None, effectiveness_verified=False))[1], "close")

    # GAP issue #119 item 5: incoming supplier NCs are on lots that are not in operating_lot_rejections.
    @unittest.expectedFailure
    def test_incoming_supplier_nc_opens_a_case(self):
        """An open NC on an incoming supplier lot triggers a QA case.
        SOP: https://www.epsilon3.io/behind-the-console/8-simple-steps-of-nonconformance-management
        """
        engine = Fake()
        engine.state["operating_lot_rejections"] = {}
        engine.company_erp = Mock()
        engine.company_erp.state = {"lots": {"RM-LOT-1": dict(item="RM-A", source="supplier receipt")}}
        qms = Mock()
        qms.list.return_value = [dict(name="NC-009", creation="2027-01-05", modified="m", status="Open")]
        with patch.object(roles, "qa_actor", return_value=qms):
            self.assertEqual([r["record"] for r in roles.qa_trigger(engine)], ["NC-009"])

    # GAP issue #119 item 5: closing an NC leaves its Quality Actions (corrective-action records) open.
    @unittest.expectedFailure
    def test_close_also_closes_linked_quality_actions(self):
        """Closing an NC also updates its linked Quality Action. SOP: none in the spec ([Judgment])."""
        qms = Mock()
        qms.get.return_value = dict(name="NC-001", status="Resolved")
        qms.list.return_value = [dict(name="QACT-1", status="Open", custom_qms_non_conformance="NC-001")]
        engine = Fake()
        with patch.object(roles, "qa_actor", return_value=qms):
            roles.qa_close(engine, dict(key="k", record="NC-001"), nc(), dict(reason="done"))
        self.assertIn(ACTION, [call.args[0] for call in qms.update.call_args_list])

    # GAP (spec section 2; issue #119 to-do): no action to ask for a lab retest or supplier 8D report.
    @unittest.expectedFailure
    def test_qa_can_request_information(self):
        """QA_MGR can choose request_info on an NC.
        SOP: https://www.epsilon3.io/behind-the-console/8-simple-steps-of-nonconformance-management
        """
        self.assertIn("request_info", QA["actions"])
