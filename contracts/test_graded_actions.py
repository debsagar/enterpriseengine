"""Graded role actions are grounded, held within authority, and executed in ERP and mail (R9, R18)."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from environments.norvale.native import judgment
from environments.norvale.native.company import Engine


def sent(total=100):
    return judgment.prompt("SCM_MGR", "2027-01-02", {"observations": {"grand_total": total}}, "Approve?")


def answer(action="approve", **extra):
    return dict(action=action, reason="within limit", cites=["evidence.observations.grand_total"]) | extra


class Grading(unittest.TestCase):
    def test_grounded_approvals_pass(self):
        self.assertEqual(judgment.graded(sent(), answer())["action"], "approve")
        conditional = answer("approve_with_condition", condition="Confirm delivery date")
        self.assertEqual(judgment.graded(sent(), conditional)["action"], "approve_with_condition")

    def test_approvals_are_held_without_evidence_condition_or_authority(self):
        for prompt, proposed in [
            (sent(), answer(cites=[])),
            (sent(), answer(cites=["evidence.observations.supplier_rating"])),
            (sent(), answer("approve_with_condition")),
            (sent(30000), answer()),
        ]:
            held = judgment.graded(prompt, proposed)
            self.assertEqual((held["action"], held["held_from"]), ("request_info", proposed["action"]))
        self.assertIn("CFO", judgment.graded(sent(30000), answer())["message"])
        with self.assertRaises(ValueError):
            judgment.graded(sent(), answer("defer"))

    def test_cites_resolve_by_flag_item_and_index(self):
        node = {"facts": {"flags": [{"flag": "quote_expired"}]}, "stock": [{"item_code": "SLES", "qty": 1}]}
        for path in ("facts.flags.quote_expired", "stock.SLES.qty", "stock[0].qty"):
            self.assertTrue(judgment.resolves(node, path), path)
        for path in ("facts.flags.other", "stock.1", "stock.0.rate"):
            self.assertFalse(judgment.resolves(node, path), path)


class Execution(unittest.TestCase):
    def review_change(self, action, **extra):
        engine = Engine.__new__(Engine)
        engine.day = 2
        change = dict(item="BOT250", level=618, requested_day=0)
        engine.state = dict(ops=dict(purchases={}, changes={"change:BOT250": change}))
        engine.wh = dict(rm="Raw", hold="Hold")
        engine.catalog = SimpleNamespace(source_list={"BOT250": "PACK"})
        engine.get = Mock(return_value={"name": "TODO-1"})
        engine.observe = Mock(return_value=[{"item_code": "BOT250", "actual_qty": 901}])
        engine.observed_usage = Mock(return_value={})
        engine.reorder_level = Mock(return_value=0)
        engine.choose = Mock(return_value=dict(action=action, reason="why", message="note", **extra))
        engine.persist = engine.log = lambda *a, **kw: None
        engine.api = {"SCM_MGR": Mock()}
        engine.write, engine.mail = Mock(), Mock()
        engine.approvals()
        written = {call.args[0].split(":")[0]: call.args[2] for call in engine.write.call_args_list}
        (_, sender, recipient, _, body), _ = engine.mail.call_args
        self.assertEqual((sender, recipient), ("SCM_MGR", "BUYER"))
        self.assertIn("reorder request TODO-1: " + action.replace("_", " "), body)
        self.assertEqual(engine.state["outbox"], {})
        return change, written, engine.api["SCM_MGR"], body

    def test_each_action_writes_erp_and_mail(self):
        change, written, _, _ = self.review_change("approve")
        self.assertEqual((list(written), change["approved_day"]), (["approval"], 2))
        change, written, _, body = self.review_change("approve_with_condition", condition="Count the bin")
        self.assertEqual(sorted(written), ["approval", "condition"])
        self.assertIn("Count the bin", written["condition"]["description"])
        self.assertIn("Count the bin", body)
        change, written, api, _ = self.review_change("reject")
        api.update.assert_called_once_with("ToDo", "TODO-1", {"status": "Cancelled"})
        self.assertEqual((written, change["rejected_day"]), ({}, 2))
        change, written, _, _ = self.review_change("request_info")
        self.assertEqual((written["info"]["description"], change["review_after"]), ("note", 9))

    def test_each_action_on_a_purchase_order_writes_erp_and_mail(self):
        workflow = {"approve": "Approve", "approve_with_condition": "Approve", "reject": "Reject", "request_info": None}
        for action, step in workflow.items():
            line = dict(item_code="SLES", qty=48, rate=1.42, amount=68.16)
            po = dict(name="PO-1", docstatus=0, supplier="Brenner", grand_total=68.16, items=[line])
            engine = Engine.__new__(Engine)
            engine.day, engine.config, engine.wh = 2, {}, dict(rm="Raw", hold="Hold")
            engine.state = dict(decisions={}, ops=dict(purchases={"po": {"day": 1}}, changes={}))
            engine.get = Mock(return_value=po)
            engine.observe, engine.observed_usage = Mock(return_value=[]), Mock(return_value={})
            engine.purchase_decision = Mock(
                return_value=dict(action=action, reason="why", message="note", condition="Check delivery")
            )
            engine.persist, engine.log, engine.mail, engine.write = Mock(), Mock(), Mock(), Mock()
            engine.schedule_supplier_arrival = Mock()
            engine.api = {"SCM_MGR": Mock(), "BUYER": Mock()}
            engine.approvals()
            applied = [c.args[1]["action"] for c in engine.api["SCM_MGR"].call.call_args_list]
            self.assertEqual(applied, [step] if step else [], action)
            written = [c.args[0].split(":")[0] for c in engine.write.call_args_list]
            expected = {"approve_with_condition": ["condition"], "request_info": ["pending"]}
            self.assertEqual(written, expected.get(action, []), action)
            self.assertEqual(engine.schedule_supplier_arrival.called, step == "Approve", action)
            self.assertIn("PO-1: " + action.replace("_", " "), engine.mail.call_args.args[4])

    def test_failed_mail_stays_queued_and_is_retried(self):
        engine = Engine.__new__(Engine)
        engine.state, engine.persist = {}, lambda: None
        engine.mail = Mock(side_effect=[TimeoutError("pending"), None])
        with self.assertRaises(TimeoutError):
            engine.notify("outcome:1", "SCM_MGR", "BUYER", "s", "b")
        self.assertIn("outcome:1", engine.state["outbox"])
        engine.send_outbox()
        self.assertEqual(engine.state["outbox"], {})


if __name__ == "__main__":
    unittest.main()
