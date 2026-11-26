"""Long operating runs: restart, heartbeat, stall, spend ceiling, preflight and provider pinning (R10, R18).

Offline: providers, ERP, mail and Docker are doubles; the engine checkpoint is real.
"""

import json
import os
import subprocess
import time
import unittest
from email.utils import format_datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from environments.norvale.native import company, company_state, long_run, router
from environments.norvale.native.long_run import PreflightError, SpendCeilingExceeded, Stalled

LIMITS = {long_run.CEILING: "5", long_run.JEV_PRICE: "1", "OPENROUTER_API_KEY": "router-key"}


def environment(**values):
    return patch.dict(os.environ, values, clear=True)


def judge():
    engine = company.Engine.__new__(company.Engine)
    engine.day, engine.state = 1, {"decisions": {}}
    engine.persist, engine.log = lambda: None, lambda *a, **kw: None
    return engine


def provider(sent, usage):
    def answer(name, request, on_attempt):
        sent.append(request)
        reply = dict(choices=[dict(finish_reason="stop", message=dict(content='{"action": "approve"}'))])
        return dict(reply, usage=usage) if usage else reply

    return patch.object(router, "provider_json", answer)


class Spending(unittest.TestCase):
    def test_key_endpoint_and_model_come_from_one_provider(self):
        sent = []
        with environment(OPENAI_API_KEY="openai-key", OPENROUTER_API_KEY="router-key"), provider(sent, None):
            judge().decision("po", "SCM_MGR", {}, "Approve?")
        self.assertEqual(sent[0].full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(sent[0].headers["Authorization"], "Bearer router-key")
        self.assertEqual(json.loads(sent[0].data)["model"], "openai/gpt-4.1-mini")
        with environment(OPENAI_API_KEY="openai-key"):
            self.assertEqual(long_run.model_endpoint(), ("openai-key", "https://api.openai.com/v1", "gpt-4.1-mini"))

    def test_ceiling_stops_the_next_call_without_a_decision(self):
        sent, engine = [], judge()
        with environment(**{**LIMITS, long_run.CEILING: "0.001"}), provider(sent, {"cost": 0.0006}):
            engine.decision("first", "SCM_MGR", {}, "Approve?")
            engine.decision("second", "SCM_MGR", {}, "Approve?")
            with self.assertRaises(SpendCeilingExceeded):
                engine.decision("third", "SCM_MGR", {}, "Approve?")
        self.assertEqual(len(sent), 2)
        self.assertNotIn("third", engine.state["decisions"])
        self.assertAlmostEqual(engine.state["spend"]["usd"], 0.0012)

    def test_an_unpriced_reply_is_not_used_under_a_ceiling(self):
        engine = judge()
        with environment(**LIMITS), provider([], {"prompt_tokens": 9}):
            with self.assertRaises(SpendCeilingExceeded):
                engine.decision("po", "SCM_MGR", {}, "Approve?")
        self.assertEqual(engine.state["decisions"], {})
        self.assertEqual(engine.state["spend"]["unpriced_calls"], 1)

    def test_an_unpriced_call_blocks_after_restart_until_reconciled(self):
        sent, engine = [], judge()
        engine.state["spend"] = dict(usd=0.0, calls=1, unpriced_calls=1)  # as saved by the stopped process
        with environment(**LIMITS), provider(sent, {"cost": 0.1}):
            with self.assertRaisesRegex(SpendCeilingExceeded, long_run.RECONCILED):
                engine.decision("po", "SCM_MGR", {}, "Approve?")
            self.assertEqual(sent, [])
            os.environ[long_run.RECONCILED] = "1"
            engine.decision("po", "SCM_MGR", {}, "Approve?")
        self.assertEqual(len(sent), 1)

    def test_router_tokens_are_priced_and_the_ceiling_stops_routing(self):
        engine = judge()
        routed = dict(judgment=False, response=dict(usage=dict(input_tokens=900_000, output_tokens=100_000)))
        with environment(**LIMITS), patch.object(router, "route", return_value=routed) as route:
            engine.choose("a", "SCM_MGR", {"supplier": "S"}, "Review")
            self.assertAlmostEqual(engine.state["spend"]["usd"], 1.0)
            os.environ[long_run.CEILING] = "1"
            with self.assertRaises(SpendCeilingExceeded):
                engine.choose("b", "SCM_MGR", {"supplier": "S"}, "Review")
        route.assert_called_once()
        self.assertNotIn("b", engine.state["decisions"])


class Watching(unittest.TestCase):
    def operating(self, run):
        engine = company.Engine.__new__(company.Engine)
        engine.run, engine.day, engine.event = Path(run), 0, "setup"
        engine.config = dict(company_operating=True)
        engine.state = {"completed": [], "decisions": {}}
        engine.log = lambda *a, **kw: None
        return engine

    def test_each_stage_writes_a_heartbeat_and_a_stop_records_why(self):
        with TemporaryDirectory() as tmp:
            engine = self.operating(tmp)
            engine.docs = {}
            engine.events = lambda: iter([(2, "d0002:service", lambda: None)])
            engine.run_phase = lambda day, key, action: None
            engine.run_stage("d0002:service")
            beat = json.loads((Path(tmp) / "heartbeat.json").read_text())
            self.assertEqual((beat["business_day"], beat["stage"]), (2, "d0002:service"))
            self.assertEqual(beat["spend"]["usd"], 0)
            with patch.object(engine, "step", side_effect=SpendCeilingExceeded("reached")):
                with self.assertRaises(SpendCeilingExceeded):
                    engine.run_all()
            beat = json.loads((Path(tmp) / "heartbeat.json").read_text())
            self.assertEqual(beat["stopped"], "SpendCeilingExceeded: reached")

    def test_stall_guard_interrupts_only_a_silent_run(self):
        with TemporaryDirectory() as tmp:
            trace = Path(tmp) / "trace.jsonl"
            with long_run.stall_guard(tmp, minutes=0.005):
                for _ in range(10):
                    trace.write_text("progress")
                    time.sleep(0.05)
            with self.assertRaises(Stalled), long_run.stall_guard(tmp, minutes=0.002):
                try:
                    time.sleep(2)
                except Exception:
                    self.fail("A broad handler swallowed the stall")


class Preflight(unittest.TestCase):
    def check(self, free=100 * 10**9, refuse=None, env=LIMITS):
        calls = []

        def docker(command, **kwargs):
            calls.append(command[1:3])
            failed = command[1:3] == refuse
            return subprocess.CompletedProcess(command, int(failed), "/", "all address pools are in use")

        with environment(**env), patch.object(long_run.subprocess, "run", docker):
            with patch.object(long_run.shutil, "disk_usage", return_value=SimpleNamespace(free=free)):
                long_run.preflight("/data/new-run")
        return calls

    def test_passes_after_creating_and_removing_a_network(self):
        self.assertEqual(self.check(), [["info", "--format"], ["network", "create"], ["network", "rm"]])

    def test_refuses_missing_limits_low_disk_and_exhausted_networks(self):
        for name in LIMITS:
            with self.subTest(name), self.assertRaisesRegex(PreflightError, "Set "):
                self.check(env={k: v for k, v in LIMITS.items() if k != name})
        direct = {long_run.CEILING: "5", long_run.JEV_PRICE: "1", "OPENAI_API_KEY": "openai-key"}
        with self.assertRaisesRegex(PreflightError, "usage.cost"):
            self.check(env=direct)
        with self.assertRaisesRegex(PreflightError, "GB free"):
            self.check(free=10**9)
        with self.assertRaisesRegex(PreflightError, "address pools"):
            self.check(refuse=["network", "create"])


class Crash(BaseException):
    """The process dies: nothing after this point runs."""


def controller(run):
    """A fresh process over the run's saved checkpoint."""
    engine = company.Engine.__new__(company.Engine)
    engine.run, engine.additions, engine.day, engine.event = Path(run), None, 0, "d0000:test"
    engine.config = dict(company.DEFAULT_CONFIG)
    engine._state = company_state.load(run, {"versions": {}, "docs": {}, "completed": [], "decisions": {}, "day": 0})
    engine.advance_clock, engine.log = lambda target: None, lambda *a, **kw: None
    engine.clock()  # as Engine.__init__ does, before anything holds a reference into the state
    engine.docs = engine.state["docs"]
    return engine


class Restart(unittest.TestCase):
    def test_order_created_before_a_crash_is_found_not_repeated(self):
        orders = []

        class Erp:
            def create(self, doctype, data):
                orders.append(dict(data, name=f"SO-{len(orders) + 1}", docstatus=0))
                if len(orders) == 1 and not self.crashed:
                    self.crashed = True
                    raise Crash
                return orders[-1]

            def list(self, doctype, filters):
                wanted = filters["custom_norvale_operation"]
                return [row for row in orders if row["custom_norvale_operation"] == wanted]

            def get(self, doctype, name):
                return next(row for row in orders if row["name"] == name)

        erp = Erp()
        erp.crashed = False
        admin = SimpleNamespace(list=lambda *a: [{"name": "operation-field"}])
        with TemporaryDirectory() as run:
            for attempt in range(2):
                engine = controller(run)
                engine.api, engine.__dict__["admin"] = {"SALES": erp}, admin
                if not attempt:
                    with self.assertRaises(Crash):
                        engine.write("order-1", "Sales Order", {"customer": "C"}, "SALES")
                else:
                    self.assertEqual(engine.write("order-1", "Sales Order", {"customer": "C"}, "SALES")["name"], "SO-1")
        self.assertEqual(len(orders), 1)

    def test_email_accepted_before_a_crash_is_not_sent_again(self):
        box, submissions = [], []

        class Mailbox:
            def __init__(self, identity, secret, endpoints):
                self.addr = identity["mail"]

            def send_mail(self, to, subject, text, when, *, message_id, on_status):
                on_status("uncertain")
                submissions.append(message_id)
                box.append(
                    dict(
                        uid=str(len(box) + 1), subject=subject, message_id=message_id, body=text + "\n", cc="",
                        attachments=[], content_type="text/plain", date=format_datetime(when),
                        **{"from": self.addr, "to": to[0]},
                    )
                )  # fmt: skip
                raise Crash

            def inbox(self, limit):
                return [row for row in box if row["to"] == self.addr]

            def read_mail(self, uid, body_limit):
                return next(row for row in box if row["uid"] == uid)

        with TemporaryDirectory() as run, patch("environments.norvale.agents.comms.Comms", Mailbox):
            company.save(Path(run) / "env/secrets.json", {"CEO": {}, "COO": {}})
            for attempt in range(2):
                engine = controller(run)
                engine.eps, engine.stack = {}, SimpleNamespace(settle=lambda: None)
                if not attempt:
                    with self.assertRaises(Crash):
                        engine.mail("mandate", "CEO", "COO", "Mandate", "Run the plant.")
                else:
                    engine.mail("mandate", "CEO", "COO", "Mandate", "Run the plant.")
                    self.assertEqual(engine.state["mail_operations"]["mandate"]["status"], "confirmed")
        self.assertEqual(len(submissions), 1)


if __name__ == "__main__":
    unittest.main()
