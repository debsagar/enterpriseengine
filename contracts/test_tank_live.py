"""Offline TK-101 inside a running Norvale company: role decision, fixed records, live fork.

A scripted proof, not an agent run: the company's setup stage and every event run through the
Norvale handler and the engine queue, but ERPNext is not started (its setup calls are stubbed)
and QMS, ERP and mail are the offline doubles. The two branches differ only in the policy given
to the SHIFT_OP role after the fork. Everything compared here is read from exported records.
"""

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from environments.consumers import tank
from environments.norvale.native import company, company_state, company_tank
from test_tank_fork import CONFIG, ORDERS

TANK = {
    **CONFIG,
    "role": "SHIFT_OP",
    "orders": ORDERS,
    "policies": {
        "protect-quality": {"on_alarm": {"pvT": "pause"}, "default": "continue"},
        "keep-running": {"on_alarm": {}, "default": "continue"},
    },
}
SETUP_ERP = ("configure", "workflow_setup", "finance_costs", "seed_opening_orders", "write")


def open_company(run):
    run.mkdir(parents=True, exist_ok=True)
    if not (run / "config.json").exists():
        (run / "config.json").write_text(json.dumps(dict(company.DEFAULT_CONFIG, tank=TANK)))
    controller = company.Engine.__new__(company.Engine)
    controller.run, controller.additions = run, None
    controller.config = json.loads((run / "config.json").read_text())
    controller._state = company_state.load(
        run, {"versions": {}, "docs": {}, "completed": [], "decisions": {}, "day": 0}
    )
    controller.advance_clock = lambda target: None  # no Docker clock offline
    controller.log = lambda *args, **kwargs: None
    return controller


def setup(controller):
    """Run the company's real recurring-setup stage, with only its ERPNext calls stubbed."""
    clock = controller.clock()
    clock.schedule("recurring-setup", "company-stage", 720, payload={})
    stubs = [patch.object(controller, name, create=True) for name in SETUP_ERP]
    with patch("environments.norvale.native.systems_v2.identities", return_value={}):
        for stub in stubs:
            stub.start()
        try:
            clock.run(until=720)
        finally:
            for stub in stubs:
                stub.stop()
    return clock


def at_most(records, minute):
    return [r for r in records if r["at"] <= minute]


class Tk101InNorvale(unittest.TestCase):
    def test_pause_and_continue_branches_share_records_until_the_fork(self):
        with patch.dict(os.environ, {"NORVALE_OFFLINE": "1"}), TemporaryDirectory() as directory:
            root = Path(directory)
            source = open_company(root / "source")
            clock = setup(source)

            # Setup (A) registered the plant through the company, once.
            self.assertEqual(source.state["tank"], {"component": "process.tank", "registered_at": 720})
            self.assertEqual(clock.state[tank.COMPONENT]["origin"], 720)

            for minute in range(720, 720 + CONFIG["horizon_minutes"], CONFIG["chunk_minutes"]):
                clock.run(until=minute)
                if clock.state[tank.COMPONENT]["alarm"]:
                    break
            fork_at = clock.now
            self.assertEqual(list(clock.state[tank.COMPONENT]["alarm"]["alarms"]), ["pvT"])
            at_fork = clock.export()["records"]
            self.assertEqual({r["batch"] for r in at_fork}, {"L1"})  # L2 is not inspected yet
            self.assertEqual({r["system"] for r in at_fork}, {"qms", "erp"})

            # A fork needs a snapshot hook for the attached systems; the bare engine refuses.
            with self.assertRaises(ValueError):
                clock.fork(root / "bare")
            with patch.object(source, "eps", {"erp": "http://localhost:8000"}, create=True):
                with self.assertRaises(RuntimeError):
                    source.fork_run(root / "live")
            self.assertFalse((root / "live" / company_state.ENGINE_DIR).exists())

            branches = {}
            for name, policy in (("pause", "protect-quality"), ("continue", "keep-running")):
                path = source.fork_run(root / name)
                self.assertEqual(path, root / name / company_state.ENGINE_DIR)
                branch = open_company(root / name)
                self.assertEqual(branch.clock().export()["records"], at_fork)  # copied doubles, same records
                company_tank.inject_policy(branch.clock(), policy)
                branch.clock().run(until=720 + CONFIG["horizon_minutes"])
                branches[name] = branch
            self.assertEqual(clock.export()["records"], at_fork)  # the source did not move

            exports = {name: branch.clock().export()["records"] for name, branch in branches.items()}
            pause, hold = exports["pause"], exports["continue"]
            self.assertEqual(at_most(pause, fork_at), at_fork)
            self.assertEqual(at_most(hold, fork_at), at_fork)
            after = {name: records[len(at_fork) :] for name, records in exports.items()}
            self.assertTrue(all(r["at"] > fork_at for rows in after.values() for r in rows))
            self.assertNotEqual(after["pause"], after["continue"])

            def pick(rows, system, kind):
                return next(r for r in rows if r["system"] == system and r["kind"] == kind and r["batch"] == "L2")

            for system, kind, field, expected in (
                ("erp", "batch_release", "status", ("released", "hold")),
                ("erp", "shortfall", "shortfall", (0, 60)),
                ("erp", "shortfall", "stock", (100, 40)),
                ("qms", "inspection", "result", ("pass", "fail")),
            ):
                self.assertEqual(
                    (pick(after["pause"], system, kind)[field], pick(after["continue"], system, kind)[field]),
                    expected,
                )
            reading = {n: pick(after[n], "qms", "inspection")["reading"] for n in after}
            self.assertGreaterEqual(reading["pause"], 96.0)
            self.assertLess(reading["continue"], 96.0)
            mail = {n: [r for r in after[n] if r["system"] == "mail"] for n in after}
            self.assertEqual([len(mail[n]) for n in mail], [1, 1])
            self.assertIn("operator action was pause", mail["pause"][0]["body"])
            self.assertIn("operator action was continue", mail["continue"][0]["body"])
            self.assertEqual(mail["pause"][0]["from"], company_tank.email("SHIFT_OP"))
            self.assertEqual(mail["pause"][0]["to"], company_tank.email("PLANT_MGR"))

            # Each new record names the engine event that caused it: the L2 inspection.
            for name, branch in branches.items():
                events = {e["id"]: e for e in branch.clock().history() if e["phase"] == "complete"}
                self.assertTrue(all(r["engine"] in events for r in after[name]))
                self.assertEqual({r["engine"] for r in after[name]}, {"tank-lot-L2"})
                self.assertGreater(events["tank-lot-L2"]["at"], fork_at)

            # The decision is the role's, from the configured policy, with its inputs recorded.
            decisions = {n: b.state["decisions"] for n, b in branches.items()}
            for name, policy, action in (
                ("pause", "protect-quality", "pause"),
                ("continue", "keep-running", "continue"),
            ):
                (key,) = decisions[name]
                self.assertTrue(key.startswith("tank-operator:"))
                decision = decisions[name][key]
                self.assertEqual(decision["mode"], "offline-operating-rule")
                self.assertEqual(decision["output"]["action"], action)
                self.assertEqual(decision["input"]["role"], "SHIFT_OP")
                self.assertEqual(decision["input"]["evidence"]["policy"], policy)
                self.assertEqual(list(decision["input"]["evidence"]["alarm"]), ["pvT"])
            self.assertEqual(source.state["decisions"], {})
            a, b = (branches[n].additions for n in ("pause", "continue"))
            self.assertEqual(a.config, b.config)  # same configuration; only the injected policy differs
            # The queues differ only by the injected policy event.
            ids = [{e["id"] for e in x.history()} for x in (a, b)]
            self.assertEqual(ids[0] ^ ids[1], {"tank-policy-protect-quality", "tank-policy-keep-running"})


if __name__ == "__main__":
    unittest.main()
