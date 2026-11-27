"""Role contracts: triggers open one case per record, offline rules follow facts, writes read back, nothing is silent."""

import json
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from environments.norvale.integrations.company_run.operating import offline_decision
from environments.norvale.native import behaviour, behaviour_roles, judgment, operations_round
from environments.norvale.native.behaviour_roles import BY_NAME, run
from environments.norvale.native.company import Engine as Company
from environments.norvale.walkthrough import ROLES


class Fake:
    """The smallest engine the generic phase touches. Writes are recorded, never sent anywhere."""

    def __init__(self, **ops):
        self.state = {"decisions": {}, "ops": {"shortage_days": {}, **ops}}
        self.day = 6
        self.config = {"behaviour_roles": True}
        self.docs, self.wh = {}, {"rm": "RM"}
        self.writes, self.mails, self.rows = [], [], {}
        self.api = {}
        self.log, self.persist = Mock(), Mock()

    def dated(self, n):
        return (date(2027, 1, 1) + timedelta(days=n)).isoformat()

    def date(self):
        return self.dated(self.day)

    def observe(self, role, doctype, filters, fields):
        return self.rows.get(doctype, [])

    def choose(self, key, role, evidence, question, actions=None):
        assert actions and key.startswith("case:")
        return offline_decision(self, key, role, evidence, question)

    def write(self, key, doctype, data, role=None):
        self.writes.append((key, doctype, data, role))
        return data

    def notify(self, key, sender, recipient, subject, headline, detail, tail=""):
        self.mails.append((key, sender, recipient))

    def cases(self, name):
        return [c for c in self.state["role_cases"].values() if c["contract"] == name]


class FinanceAPI:
    def __init__(self, invoice, order, receipt):
        self.docs = {
            ("Purchase Invoice", "PINV-1"): invoice,
            ("Purchase Order", "PO-1"): order,
            ("Purchase Receipt", "PR-1"): receipt,
        }
        self.calls = []

    def get(self, doctype, name):
        return dict(self.docs[doctype, name])

    def call(self, method, args):
        self.calls.append((method, json.loads(args["method"]) if False else args["method"]))
        invoice = self.docs["Purchase Invoice", "PINV-1"]
        invoice["on_hold"] = int(args["method"] == "block_invoice")


def line(qty, rate=10):
    return dict(item_code="RM-A", qty=qty, rate=rate, amount=qty * rate)


def finance(billed=10, received=10, supplier="S", ordered=100):
    invoice = dict(
        name="PINV-1",
        supplier=supplier,
        grand_total=billed * 10,
        items=[dict(line(billed), purchase_order="PO-1", purchase_receipt="PR-1")],
        on_hold=0,
    )
    order = dict(name="PO-1", supplier="S", grand_total=ordered)
    engine = Fake()
    engine.rows["Purchase Invoice"] = [
        dict(name="PINV-1", grand_total=billed * 10, posting_date="2027-01-05", supplier=supplier)
    ]
    engine.api["FINANCE"] = FinanceAPI(invoice, order, dict(items=[line(received)]))
    return engine


class RoleContracts(TestCase):
    def test_each_trigger_opens_one_case_per_record_and_not_again(self):
        invoices = finance()
        shortage = Fake(
            shortage_context={"RM-A": {"day": 6, "work_planned": 0}},
        )
        shortage.state["ops"]["shortage_days"] = {"RM-A": [6]}
        shortage.rows["Bin"] = [dict(item_code="RM-A", actual_qty=3)]
        ncs = [
            dict(
                name=f"NC-{i}",
                creation=f"2027-01-0{i}",
                modified="m",
                status="Open",
                batch=f"B{i}",
                item="X",
                custom_failed_inspection=f"I{i}",
            )
            for i in (3, 4)
        ]
        quality = Fake()
        quality_client = Mock()
        quality_client.get.side_effect = lambda doctype, name: dict(
            name=name, status="Open", custom_failed_inspection="I", workflow_state="Lot Rejected"
        )
        with (
            patch.object(behaviour_roles, "nonconformances", return_value=ncs),
            patch.object(behaviour_roles, "qa_actor", return_value=quality_client),
        ):
            for engine, names in (
                (invoices, ["invoice"]),
                (shortage, ["shortage"]),
                (quality, ["design_change", "nonconformance"]),
            ):
                for _ in range(2):
                    run(engine)
                for name in names:
                    records = [c["record"] for c in engine.cases(name)]
                    self.assertEqual(len(records), len(set(records)), name)
                    self.assertTrue(records, name)
        self.assertEqual(sorted(c["record"] for c in quality.cases("nonconformance")), ["NC-3", "NC-4"])
        self.assertEqual([c["record"] for c in quality.cases("design_change")], ["X"])

    def test_offline_rules_pick_a_non_approval_when_a_blocking_fact_is_present(self):
        def choice(name, evidence):
            facts = BY_NAME[name]["facts"](evidence, "2027-01-06")
            return BY_NAME[name]["offline_rule"]({**evidence, "facts": facts})[0]

        engine = finance(billed=12, received=10)
        case = dict(key="k", record="PINV-1")
        clean = behaviour_roles.invoice_evidence(finance(), case)
        over = behaviour_roles.invoice_evidence(engine, case)
        no_order = dict(clean, order=None, purchase_orders=[])
        self.assertEqual(choice("invoice", clean), "approve")
        self.assertEqual(choice("invoice", over), "hold")
        self.assertEqual(choice("invoice", no_order), "request_info")
        rejected = dict(
            inspection=dict(workflow_state="Lot Rejected"),
            engineering_review_open=False,
            escalated_recently=False,
            item="X",
        )
        self.assertEqual(choice("nonconformance", dict(rejected, same_item_nonconformances=["a"])), "close")
        self.assertEqual(choice("nonconformance", dict(rejected, same_item_nonconformances=list("abc"))), "escalate")
        self.assertEqual(
            choice(
                "nonconformance",
                dict(rejected, inspection=dict(workflow_state="Rework Authorized"), same_item_nonconformances=[]),
            ),
            "keep_open",
        )
        self.assertEqual(
            choice("nonconformance", dict(rejected, engineering_review_open=True, same_item_nonconformances=[])),
            "keep_open",
        )
        self.assertEqual(choice("shortage", dict(shortage_days=[5, 6], work_planned_today=3)), "escalate")
        self.assertEqual(choice("shortage", dict(shortage_days=[6], work_planned_today=3)), "reschedule")
        self.assertEqual(choice("shortage", dict(shortage_days=[6], work_planned_today=0)), "wait")
        self.assertEqual(
            choice("design_change", dict(nonconformances=list("ab"), change_raised_recently=False)), "no_change"
        )
        self.assertEqual(
            choice("design_change", dict(nonconformances=list("abc"), change_raised_recently=False)),
            "raise_design_change",
        )
        self.assertEqual(
            choice("design_change", dict(nonconformances=list("abc"), change_raised_recently=True)), "no_change"
        )

    def test_finance_executes_its_native_write_and_settlement_pays_only_approved_invoices(self):
        clean, held = finance(), finance(billed=12, received=10)
        for engine in (clean, held):
            engine.docs["invoice:1"] = {"doctype": "Purchase Invoice", "name": "PINV-1"}
            run(engine)
        self.assertEqual([c["action"] for c in clean.cases("invoice")], ["approve"])
        self.assertTrue(behaviour_roles.invoice_cleared(clean, "invoice:1"))
        self.assertEqual([c["action"] for c in held.cases("invoice")], ["hold"])
        self.assertEqual(held.api["FINANCE"].calls, [("run_doc_method", "block_invoice")])
        self.assertFalse(behaviour_roles.invoice_cleared(held, "invoice:1"))
        held.docs["invoice:2"] = {"doctype": "Sales Invoice", "name": "SINV-1"}
        self.assertTrue(behaviour_roles.invoice_cleared(held, "invoice:2"))

    def test_a_failed_write_leaves_the_case_open_and_it_is_escalated_once_past_due(self):
        engine = finance()
        engine.api["FINANCE"].call = Mock(side_effect=RuntimeError("ERP unreachable"))
        engine.api["FINANCE"].docs["Purchase Invoice", "PINV-1"]["grand_total"] = 120
        engine.api["FINANCE"].docs["Purchase Order", "PO-1"]["grand_total"] = 100
        run(engine)
        (case,) = engine.cases("invoice")
        self.assertEqual(case["status"], "open")
        engine.day = case["due"] + 1
        run(engine)
        run(engine)
        self.assertEqual((case["status"], len(engine.mails)), ("escalated", 1))
        self.assertEqual(engine.mails[0][1:], ("CFO", "CEO"))

    def test_qa_closes_a_resolved_lot_through_the_qms_and_reads_it_back(self):
        store = {"NC-3": dict(name="NC-3", status="Open", custom_failed_inspection="I3")}

        class QMS:
            def get(self, doctype, name):
                if doctype == "Quality Inspection":
                    return dict(name=name, workflow_state="Lot Rejected")
                return dict(store[name])

            def update(self, doctype, name, **fields):
                store[name].update(fields)

        nc = dict(name="NC-3", creation="2027-01-03", modified="m", status="Open", batch="B3", item="X")
        engine = Fake()
        with (
            patch.object(behaviour_roles, "nonconformances", return_value=[nc]),
            patch.object(behaviour_roles, "qa_actor", return_value=QMS()),
        ):
            run(engine)
        self.assertEqual(store["NC-3"]["status"], "Resolved")
        self.assertEqual([c["action"] for c in engine.cases("nonconformance")], ["close"])

    def test_run_targets_report_pass_and_fail(self):
        engine = finance()
        run(engine)
        with TemporaryDirectory() as temp:
            good = behaviour.targets(Path(temp), engine.state, 6)
            self.assertTrue(all(v for k, v in good["passed"].items() if v is not None))
            self.assertEqual(good["per_role"]["CFO"]["decisions"], 1)
            engine.state["role_cases"]["invoice:PINV-1:100:2027-01-05"]["status"] = "open"
            engine.state["role_cases"]["invoice:PINV-1:100:2027-01-05"]["due"] = 1
            bad = behaviour.targets(Path(temp), engine.state, 6)
            self.assertFalse(bad["passed"]["no_silent_backlog"])
            self.assertEqual(bad["past_due"], ["invoice:PINV-1:100:2027-01-05"])
            engine.state["operations_round"] = {"fixed_posts": 3}
            self.assertFalse(behaviour.targets(Path(temp), engine.state, 6)["passed"]["messages_grounded"])

    def test_a_model_may_choose_only_the_contract_actions_and_cannot_approve_over_an_unnamed_flag(self):
        sent = judgment.prompt(
            "CFO",
            "2027-01-06",
            dict(facts=dict(flags=[dict(flag="billed_over_received")])),
            "q",
            (),
            ["approve", "hold"],
        )
        with self.assertRaises(ValueError):
            judgment.graded(sent, dict(action="reject", reason="x"))
        held = judgment.graded(sent, dict(action="approve", reason="looks fine", cites=["facts.flags"]))
        self.assertEqual((held["action"], held["held_from"]), ("request_info", "approve"))

    def test_chat_has_one_message_per_decided_case_and_none_without_a_case(self):
        engine = finance()
        run(engine)
        engine.compose = lambda key, sender, recipient, subject, template: template
        done = {"room": "!room"}
        client = Mock()
        operations_round.case_messages(engine, done, lambda role: client)
        operations_round.case_messages(engine, done, lambda role: client)
        (post,) = client.send_chat.call_args_list
        self.assertIn("PINV-1", post.args[1])
        self.assertEqual(post.args[0], "!room")
        engine.state["role_cases"] = {}
        client.reset_mock()
        operations_round.case_messages(engine, done, lambda role: client)
        client.send_chat.assert_not_called()

    def test_the_role_phase_is_runnable_on_the_real_engine_class(self):
        engine = object.__new__(Company)
        engine.roles = {"CFO": []}  # the ERP permission table set by __init__
        engine.config = {"behaviour_roles": True, "days": 1}
        engine.state = {"completed": []}
        (action,) = [a for _, key, a in engine.recurring_events() if key == "d0000:role_cases"]
        with patch.object(behaviour_roles, "run") as phase:
            action()
        phase.assert_called_once_with(engine)

    def test_every_role_that_posts_a_case_is_invited_to_the_room(self):
        with TemporaryDirectory() as temp:
            (Path(temp) / "env").mkdir()
            (Path(temp) / "env/secrets.json").write_text(json.dumps({r["role_id"]: {} for r in ROLES}))
            engine = Fake()
            engine.run, engine.eps, engine.state = Path(temp), {}, {}
            with patch.object(operations_round, "Comms") as comms:
                comms.return_value.create_room.return_value = "!room"
                operations_round.run(engine)
        invited = comms.return_value.create_room.call_args.args[1]
        posters = {c["role"].lower() for c in behaviour_roles.CONTRACTS}
        self.assertLessEqual(posters - {operations_round.CHAIR.lower()}, set(invited))
