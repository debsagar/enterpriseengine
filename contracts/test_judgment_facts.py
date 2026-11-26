"""Code-checked facts and contradiction flags in role decision prompts (R9, R18)."""

import json
import unittest
from unittest.mock import Mock, patch

from environments.norvale.native import judgment, router
from environments.norvale.native.company import Engine

LINE = dict(item_code="BOT250", qty=468, rate=0.11, amount=51.48)
ORDER = dict(
    supplier="PackRight",
    grand_total=51.48,
    items=[LINE],
    supplier_quote=dict(supplier="PackRight", valid_till="2027-01-08", grand_total=51.48, items=[LINE]),
    coverage=[
        dict(
            item_code="BOT250",
            released_raw_materials_qty=100,
            quarantine_qty=0,
            open_po_qty_excluding_this=0,
            reorder_target_qty=500,
        )
    ],
    observed_daily_usage={"BOT250": 50},
)
# Probe run, day 2: a reorder increase to 618 was approved with 901 on hand.
CHANGE = dict(
    requested_change=dict(
        item_code="BOT250",
        requested_reorder_level=618,
        current_reorder_level=0,
        released_qty=901,
        observed_daily_usage=51.43,
        has_supplier=True,
    )
)


def flags(evidence, role="SCM_MGR", policies=()):
    return [f["flag"] for f in judgment.facts(role, evidence, "2027-01-02", policies)["flags"]]


class JudgmentFacts(unittest.TestCase):
    def test_clean_order_has_facts_and_no_flags(self):
        checked = judgment.facts("SCM_MGR", {"observations": ORDER}, "2027-01-02")
        self.assertEqual(checked["flags"], [])
        self.assertEqual(checked["stock"][0]["days_of_cover_released"], 2.0)
        self.assertEqual(checked["po_minus_quote_total"], 0)
        self.assertEqual(checked["role_approval_limit_headroom"], 24948.52)

    def test_taxes_above_the_net_total_are_not_a_contradiction(self):
        taxed = {**ORDER, "grand_total": 62.29, "net_total": 51.48}
        taxed["supplier_quote"] = dict(ORDER["supplier_quote"], grand_total=62.29)
        self.assertEqual(flags(taxed), [])
        self.assertEqual(flags({**taxed, "net_total": 60}), ["total_not_sum_of_lines"])

    def test_contradictions_are_flagged(self):
        dearer = {**LINE, "rate": 0.2, "amount": 93.6}
        self.assertEqual(
            flags({**ORDER, "items": [dearer], "grand_total": 93.6}), ["quote_line_mismatch", "quote_total_mismatch"]
        )
        self.assertIn("quote_line_mismatch", flags({**ORDER, "items": [LINE, LINE]}))
        covered = dict(ORDER["coverage"][0], released_raw_materials_qty=901, open_po_qty_excluding_this=12)
        self.assertEqual(
            flags({**ORDER, "coverage": [covered]}), ["stock_above_reorder_target", "duplicate_open_order"]
        )
        self.assertEqual(flags(CHANGE), ["stock_at_or_above_requested_level"])
        self.assertIn("over_role_approval_limit", flags({"grand_total": 30000}))
        policy = dict(suppliers=["PackRight"], items=["BOT250"], approval_limit=50)
        self.assertEqual(flags(ORDER, policies=[policy]), ["over_policy_approval_limit"])
        self.assertEqual(flags(ORDER, policies=[dict(policy, approval_limit=100), policy]), [])
        self.assertEqual(flags(ORDER, policies=[dict(policy, approval_limit=51.479)]), ["over_policy_approval_limit"])
        self.assertEqual(flags({"native_balances": {"Cash": 5, "Stock": -4}}, "CFO"), ["ledger_not_balanced"])

    def test_open_po_coverage_uses_stock_units_and_only_available_orders(self):
        engine = Engine.__new__(Engine)
        line = dict(item_code="BOT250", qty=3, received_qty=1, conversion_factor=12)
        orders = [
            dict(name="open", docstatus=1, items=[line]),
            dict(name="draft", docstatus=0, items=[dict(line, conversion_factor=1)]),
            dict(name="hold", docstatus=1, status="On Hold", items=[line]),
            dict(name="closed", docstatus=1, status="Closed", items=[line]),
        ]
        self.assertEqual(engine.open_po_qty(orders, "BOT250"), 26)
        self.assertEqual(engine.open_po_qty(orders, "BOT250", excluding="open"), 2)

    def test_order_rejected_in_this_pass_is_not_open_supply_for_the_next(self):
        draft = dict(docstatus=0, supplier="PackRight", grand_total=51.48, items=[LINE])
        native = {key: dict(draft, name=key) for key in ("first", "second")}
        engine = Engine.__new__(Engine)
        engine.day, engine.config, engine.wh = 2, {}, dict(rm="Raw", hold="Hold")
        engine.state = dict(decisions={}, ops=dict(purchases={key: {"day": 1} for key in native}, changes={}))
        engine.get = lambda key, role: dict(native.get(key, dict(draft, name=key)))
        engine.observe = Mock(return_value=[])
        engine.observed_usage = Mock(return_value={})
        engine.persist, engine.log = Mock(), Mock()
        engine.purchase_decision = Mock(return_value=dict(action="reject", reason="no"))

        def reject(method, args):
            native[json.loads(args["doc"])["name"]]["workflow_state"] = "Rejected"

        engine.api = {"SCM_MGR": Mock(call=Mock(side_effect=reject))}
        engine.approvals()
        open_qty = [
            c.args[2]["coverage"][0]["open_po_qty_excluding_this"] for c in engine.purchase_decision.call_args_list
        ]
        self.assertEqual(open_qty, [468, 0])

    def test_facts_policy_and_limits_reach_the_model_prompt(self):
        engine = Engine.__new__(Engine)
        engine.day, engine.state = 1, {"decisions": {}}
        engine.persist, engine.log = lambda: None, lambda *a, **kw: None
        sent = []

        def provider(name, request, on_attempt):
            sent.append(json.loads(json.loads(request.data)["messages"][0]["content"]))
            return {"choices": [{"finish_reason": "stop", "message": {"content": '{"action": "reject"}'}}]}

        with patch.dict("os.environ", {"NORVALE_OFFLINE": "0", "OPENAI_API_KEY": "stub"}):
            with patch.object(router, "provider_json", provider):
                engine.decision("change", "SCM_MGR", {"observations": CHANGE}, "Approve?")
        self.assertEqual(sent[0]["facts"]["flags"][0]["flag"], "stock_at_or_above_requested_level")
        self.assertEqual(sent[0]["policy"]["authority_limits"], {"po_approval_eur": 25000})
        self.assertEqual(sent[0]["policy"]["effective_policies"], [])


if __name__ == "__main__":
    unittest.main()
