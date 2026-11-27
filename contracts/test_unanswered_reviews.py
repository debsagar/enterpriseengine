"""A case that keeps asking for information is deferred and escalated once, not reviewed forever (#108)."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from environments.norvale.native.company import Engine

LINE = dict(item_code="SLES", qty=48, rate=1.42, amount=68.16)
ASK = dict(action="request_info", reason="Confirm date", message="Which delivery date?")


def company(purchases, changes):
    engine = Engine.__new__(Engine)
    engine.day, engine.config, engine.wh = 2, {}, dict(rm="Raw", hold="Hold")
    engine.state = dict(decisions={}, ops=dict(purchases=purchases, changes=changes))
    engine.observe, engine.observed_usage = Mock(return_value=[]), Mock(return_value={})
    engine.persist, engine.log, engine.mail, engine.write = Mock(), Mock(), Mock(), Mock()
    engine.api = {"SCM_MGR": Mock(), "BUYER": Mock()}
    return engine


def run(engine, days=40):
    for engine.day in range(2, days):
        engine.approvals()
    return [c.args for c in engine.write.call_args_list if c.args[0].startswith("escalation:")]


class UnansweredReviews(TestCase):
    def test_purchase_is_reviewed_three_times_then_escalated_once(self):
        purchase = {"day": 1}
        engine = company({"po": purchase}, {})
        engine.get = Mock(return_value=dict(name="PO-1", docstatus=0, supplier="B", grand_total=68.16, items=[LINE]))
        engine.purchase_decision = Mock(return_value=ASK)
        ((_, _, task, _),) = run(engine)
        self.assertEqual(engine.purchase_decision.call_count, 3)
        self.assertEqual((task["allocated_to"], task["reference_name"]), ("cfo@norvale.test", "PO-1"))
        self.assertIn("Which delivery date?", task["description"])
        self.assertEqual(purchase["deferred_day"], 6)

    def test_provider_outage_does_not_count(self):
        engine = company({"po": {"day": 1}}, {})
        engine.get = Mock(return_value=dict(name="PO-1", docstatus=0, supplier="B", grand_total=68.16, items=[LINE]))

        def outage(key, *args, **kwargs):
            engine.state["decisions"][key] = dict(mode="provider-unavailable")
            return dict(action="defer", reason="offline", message="pending")

        engine.purchase_decision = Mock(side_effect=outage)
        self.assertEqual(run(engine, 12), [])
        self.assertEqual(engine.purchase_decision.call_count, 5)

    def test_reorder_request_is_escalated_after_three_reviews(self):
        change = dict(item="BOT250", level=618, requested_day=0)
        engine = company({}, {"change:BOT250": change})
        engine.catalog = SimpleNamespace(source_list={})
        engine.get = Mock(return_value={"name": "TODO-1"})
        engine.reorder_level = Mock(return_value=0)
        engine.choose = Mock(return_value=ASK)
        self.assertEqual(len(run(engine)), 1)
        self.assertEqual((engine.choose.call_count, change["deferred_day"]), (3, 16))

    def test_deferred_draft_is_not_counted_as_supply(self):
        draft = dict(docstatus=0, supplier="B", grand_total=68.16, items=[LINE])
        native = {key: dict(draft, name=key) for key in ("stuck", "fresh")}
        engine = company({"stuck": {"day": 1, "deferred_day": 1}, "fresh": {"day": 1}}, {})
        engine.get = lambda key, role: native.get(key, dict(draft, name=key))
        engine.purchase_decision = Mock(return_value=ASK)
        engine.approvals()
        (call,) = engine.purchase_decision.call_args_list
        self.assertEqual(call.args[2]["coverage"][0]["open_po_qty_excluding_this"], 0)
        self.assertTrue(Engine.parked({"deferred_day": 1}, draft))
        self.assertFalse(Engine.parked({"deferred_day": 1}, dict(draft, docstatus=1)))
