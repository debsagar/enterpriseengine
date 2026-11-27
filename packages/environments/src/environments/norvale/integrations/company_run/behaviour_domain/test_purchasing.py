"""SC_MGR (role id SCM_MGR in code): purchase order and reorder-level approvals.

facts = judgment.facts, approval guard = judgment.graded, offline rule = operating.offline_decision.
"""

import unittest

from environments.norvale.integrations.company_run.operating import offline_decision
from environments.norvale.integrations.company_run.test_behaviour_roles import Fake
from environments.norvale.native import judgment

TODAY = "2026-11-27"


def line(qty=450, rate=10, item="RM-01"):
    return dict(item_code=item, qty=qty, rate=rate, amount=qty * rate)


def po(qty=450, quote=None, coverage=None):
    """A PO that matches its quote, with stock below target. qty * 10 EUR is the total."""
    offer = dict(supplier="SUP-A", valid_till="2026-12-04", grand_total=qty * 10, items=[line(qty)])
    return dict(
        supplier="SUP-A",
        grand_total=qty * 10,
        items=[line(qty)],
        supplier_quote={**offer, **(quote or {})},
        coverage=[
            {
                "item_code": "RM-01",
                "released_raw_materials_qty": 10,
                "open_po_qty_excluding_this": 0,
                "reorder_target_qty": 100,
                **(coverage or {}),
            }
        ],
    )


def sent(evidence, policies=()):
    return judgment.prompt("SCM_MGR", TODAY, evidence, "Decide this PO.", policies)


def flags(evidence):
    return {f["flag"] for f in judgment.facts("SCM_MGR", evidence, TODAY)["flags"]}


def approve(**over):
    return {"action": "approve", "reason": "ok", "cites": ["evidence.grand_total"], **over}


def offline(evidence):
    engine = Fake()
    engine.date = lambda: TODAY
    return offline_decision(engine, "po-review:1", "SCM_MGR", evidence, "Decide this PO.")["action"]


class Purchasing(unittest.TestCase):
    def test_1_clean_po_within_limits_is_approved(self):
        """A PO matching an active quote, within role and policy limits, is approved. SOP: https://ramp.com/blog/accounts-payable/3-way-match"""
        policy = dict(approval_limit=5000, suppliers=["SUP-A"], items=["RM-01"])
        packet = sent(po(), [policy])
        self.assertEqual(packet["facts"]["flags"], [])
        self.assertEqual(judgment.graded(packet, approve())["action"], "approve")
        self.assertEqual(offline(po()), "approve")

    def test_2_over_role_limit_is_held_and_sent_to_cfo(self):
        """A 30,000 EUR PO over the 25,000 limit cannot be approved by SC_MGR. SOP: none in the spec ([Judgment])."""
        packet = sent(po(qty=3000))
        self.assertIn("over_role_approval_limit", {f["flag"] for f in packet["facts"]["flags"]})
        held = judgment.graded(
            packet,
            approve(reason="over_role_approval_limit accepted", cites=["facts.role_approval_limit_headroom"]),
        )
        self.assertEqual((held["action"], held["held_from"]), ("request_info", "approve"))
        self.assertIn("CFO", held["message"])

    # Regression for debsagar/enterpriseengine#120.
    def test_2_offline_rule_does_not_approve_over_the_limit(self):
        """The free offline rule must not approve a PO over SC_MGR's limit. SOP: none in the spec ([Judgment])."""
        self.assertNotEqual(offline(po(qty=3000)), "approve")

    def test_3_expired_quote_is_flagged_and_not_approved_offline(self):
        """An expired supplier quote is flagged and the offline rule defers. SOP: none in the spec ([Judgment])."""
        evidence = po(quote=dict(valid_till="2026-11-01"))
        self.assertIn("quote_expired", flags(evidence))
        self.assertEqual(offline(evidence), "defer")

    # Regression for debsagar/enterpriseengine#120.
    def test_3_approval_that_ignores_expired_quote_is_held(self):
        """graded() holds a PO approval that does not name quote_expired. SOP: none in the spec ([Judgment])."""
        out = judgment.graded(sent(po(quote=dict(valid_till="2026-11-01"))), approve())
        self.assertEqual(out["action"], "request_info")

    def test_4_quote_from_another_supplier_is_flagged_and_not_approved_offline(self):
        """A quote from a different supplier than the PO is flagged and deferred. SOP: https://ramp.com/blog/accounts-payable/3-way-match"""
        evidence = po(quote=dict(supplier="SUP-B"))
        self.assertIn("quote_supplier_mismatch", flags(evidence))
        self.assertEqual(offline(evidence), "defer")

    # Regression for debsagar/enterpriseengine#120.
    def test_4_approval_that_ignores_supplier_mismatch_is_held(self):
        """graded() holds a PO approval that does not name quote_supplier_mismatch. SOP: https://ramp.com/blog/accounts-payable/3-way-match"""
        out = judgment.graded(sent(po(quote=dict(supplier="SUP-B"))), approve())
        self.assertEqual(out["action"], "request_info")

    def test_5_duplicate_open_order_and_stock_above_target_are_flagged(self):
        """Stock 80 plus 40 already on order against target 100 raises both flags. SOP: none in the spec ([Judgment])."""
        evidence = po(coverage=dict(released_raw_materials_qty=80, open_po_qty_excluding_this=40))
        self.assertLessEqual({"stock_above_reorder_target", "duplicate_open_order"}, flags(evidence))

    # Regression for debsagar/enterpriseengine#120.
    def test_5_offline_rule_does_not_approve_a_duplicate_order(self):
        """The offline rule must not approve a duplicate PO (extends scenario 5). SOP: none in the spec ([Judgment])."""
        evidence = po(coverage=dict(released_raw_materials_qty=80, open_po_qty_excluding_this=40))
        self.assertNotEqual(offline(evidence), "approve")

    def test_6_nonpositive_line_is_flagged(self):
        """A PO line with quantity zero is flagged as invalid. SOP: none in the spec ([Judgment])."""
        evidence = dict(supplier="SUP-A", grand_total=0, items=[line(qty=0)])
        self.assertIn("nonpositive_line", flags(evidence))

    def test_7_conditional_approval_without_a_condition_is_held(self):
        """approve_with_condition with a blank condition becomes request_info. SOP: none in the spec ([Judgment])."""
        out = judgment.graded(sent(po()), approve(action="approve_with_condition", condition="   "))
        self.assertEqual(out["action"], "request_info")
        self.assertEqual(out["reason"], "The conditional approval named no condition.")

    def test_8_reorder_change_without_a_supplier_is_flagged(self):
        """Raising a reorder level on an item with no approved supplier is flagged. SOP: none in the spec ([Judgment])."""
        change = dict(
            item_code="RM-99",
            requested_reorder_level=500,
            current_reorder_level=200,
            released_qty=100,
            has_supplier=False,
        )
        self.assertIn("item_has_no_supplier", flags(dict(requested_change=change)))


class PurchasingGaps(unittest.TestCase):
    """Spec "wrong or missing" items. New evidence field names here are assumptions."""

    # GAP (issue #119 to-do; spec section 1): no approved-vendor check on a draft PO.
    @unittest.expectedFailure
    def test_po_from_unapproved_supplier_is_flagged(self):
        """A PO to a supplier not approved for the item raises a flag. SOP: https://simplerqms.com/non-conformance/"""
        self.assertTrue(flags({**po(), "approved_suppliers": {"RM-01": ["SUP-Z"]}}))

    # GAP (issue #119 to-do; spec section 1): lead time is never compared with days of cover or need date.
    @unittest.expectedFailure
    def test_lead_time_longer_than_cover_is_flagged(self):
        """2 days of cover against a 10 day lead time raises a late-delivery flag.
        SOP: https://help.sap.com/doc/9dadce5314894208e10000000a174cb4/3.6/en-US/0a98b6535fe6b74ce10000000a174cb4.html
        """
        evidence = {
            **po(coverage=dict(supplier_lead_time_days=10)),
            "observed_daily_usage": {"RM-01": 5},
            "supplier_lead_time_days": 10,
            "schedule_date": "2026-12-07",
        }
        self.assertTrue(flags(evidence))

    # GAP (issue #119 to-do; spec section 1): any non-zero price delta fires a mismatch flag.
    @unittest.expectedFailure
    def test_half_percent_price_variance_is_within_tolerance(self):
        """A PO 0.5% (5 EUR on 1000) above its quote raises no flag.
        SOP: https://learn.microsoft.com/en-us/dynamics365/finance/accounts-payable/three-way-matching-policies
        """
        evidence = po(qty=100)
        evidence["grand_total"] = 1005
        evidence["items"] = [line(qty=100, rate=10.05)]
        self.assertEqual(flags(evidence), set())
