"""Offline two-branch TK-101 what-if: steam drop, alarm, fork, operator pause vs continue.

A scripted proof, not an agent run: the operator actions are injected by the test. Both
branches use only observable records: the engine checkpoint, the AI-101 reading on each
lot's inspection, and the CompanyERP finished-supply pool that sales orders draw on.
"""

import copy
import sqlite3
import tracemalloc
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from types import SimpleNamespace

from engine import Engine
from environments.consumers import tank
from environments.norvale.native.company_erp import CompanyERP
from environments.norvale.native.recurring_extensions import RecurringSales, open_catalog

CONFIG = {
    "horizon_minutes": 1440,
    "chunk_minutes": 5,
    "feed": 15.0,
    "pause_feed_scale": 0.3,
    "steam_drop": {"at_minute": 60, "pressure": -0.75},
    "alarms": {"pvT": 65.0, "AI-101": 96.0},
    "lots": [
        {"batch": "L1", "item": "FG", "qty": 40, "at_minute": 30},
        {"batch": "L2", "item": "FG", "qty": 60, "at_minute": 600},
    ],
}
ORDERS = {"SO-1": {"item": "FG", "qty": 100}}


def open_engine(directory):
    consumer = RecurringSales({"tank": CONFIG}, None, None)
    engine = Engine(directory, consumer, seed=7, native=None, providers=None, monotonic=monotonic)
    tank.register(engine)
    return engine


class QMS:
    """Disposition from the lot's recorded AI-101 inspection reading."""

    def __init__(self, lots):
        self.lots = lots

    def disposition(self, lot, *, as_of, role):
        record = self.lots[lot["batch"]]
        ok = record["reading"] >= record["spec_min"]
        return {
            "batch": lot["batch"], "item": lot["item"], "uom": "Nos", "id": "QMS-" + lot["batch"],
            "version": "inspection-1", "status": "released" if ok else "hold", "qty": lot["qty"] if ok else 0,
            "authorized": True, "approver": "QA-1", "evidence": f"AI-101 {record['reading']}",
        }  # fmt: skip


def enterprise(engine):
    """Lot status and sales-order impact through the Norvale CompanyERP supply pool."""
    lots = copy.deepcopy(engine.state[tank.COMPONENT]["lots"])
    host = SimpleNamespace(state={}, date=lambda: "2027-01-01")
    erp = CompanyERP(host, QMS(lots))
    for batch, lot in lots.items():
        erp.state["lots"][batch] = {
            "batch": batch,
            "item": lot["item"],
            "kind": "finished",
            "qty": lot["qty"],
            "uom": "Nos",
        }
        erp.state.setdefault("synced", {})[batch] = erp.disposition(erp.state["lots"][batch], "QA")
    supply = erp.finished_supply()
    status = {b: d["status"] for b, d in erp.state["synced"].items()}
    short = {o: max(0, s["qty"] - supply.get(s["item"], 0)) for o, s in ORDERS.items()}
    return {"lots": status, "supply": supply, "order_shortfall": short}


def dump(path):
    with closing(sqlite3.connect(path)) as db:
        return list(db.iterdump())


class Tk101WhatIf(unittest.TestCase):
    def test_pause_and_continue_share_history_until_the_fork(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = open_engine(root / "source")
            for minute in range(0, CONFIG["horizon_minutes"], CONFIG["chunk_minutes"]):
                source.run(until=minute)
                if source.state[tank.COMPONENT]["alarm"]:
                    break
            alarm = source.state[tank.COMPONENT]["alarm"]
            fork_at = source.now
            self.assertEqual(list(alarm["alarms"]), ["pvT"])
            self.assertGreater(alarm["k"], CONFIG["steam_drop"]["at_minute"] * tank.SCANS)
            self.assertLessEqual(alarm["k"], fork_at * tank.SCANS)
            self.assertEqual(source.state[tank.COMPONENT]["boundaries"], [alarm])

            before = enterprise(source)
            self.assertEqual(before["lots"], {"L1": "released"})  # L2 is not yet inspected
            baseline, fork_history = dump(source._database), source.history()
            paths = {name: source.fork(root / name) for name in ("pause", "continue")}
            branches = {name: open_engine(path) for name, path in paths.items()}

            for name, branch in branches.items():  # identical at the fork, down to the SQLite dump
                self.assertEqual(dump(paths[name] / "engine.sqlite3"), baseline)
                self.assertEqual(branch.state, source.state)
                self.assertEqual(branch.history(), fork_history)
                self.assertEqual(enterprise(branch), before)

            for name, branch in branches.items():
                branch.schedule("operator-1", "tank_operator", fork_at + 1, payload={"action": name})
                branch.run(until=CONFIG["horizon_minutes"])
            pause, hold = branches["pause"], branches["continue"]

            # Source did not share state with either branch.
            self.assertEqual(dump(source._database), baseline)
            self.assertEqual(source.state[tank.COMPONENT]["operator"], None)

            for branch in (pause, hold):  # history before the fork is untouched; everything new is after it
                self.assertEqual(branch.history()[: len(fork_history)], fork_history)
                self.assertTrue(all(e["at"] > fork_at for e in branch.history()[len(fork_history) :]))
            a, b = (x.state[tank.COMPONENT] for x in (pause, hold))
            self.assertEqual(a["alarm"], b["alarm"])  # same alarm, same scan, in both branches
            self.assertEqual(a["lots"]["L1"], b["lots"]["L1"])
            self.assertNotEqual(a["plant"], b["plant"])
            self.assertEqual(a["operator"]["action"], "pause")
            self.assertEqual(b["operator"]["action"], "continue")

            self.assertGreaterEqual(a["lots"]["L2"]["reading"], 96.0)
            self.assertLess(b["lots"]["L2"]["reading"], 96.0)
            self.assertEqual(
                enterprise(pause),
                {"lots": {"L1": "released", "L2": "released"}, "supply": {"FG": 100}, "order_shortfall": {"SO-1": 0}},
            )
            self.assertEqual(
                enterprise(hold),
                {"lots": {"L1": "released", "L2": "hold"}, "supply": {"FG": 40}, "order_shortfall": {"SO-1": 60}},
            )

    def test_scan_timeline_does_not_grow_with_the_horizon(self):
        cfg = dict(CONFIG, horizon_minutes=30 * 1440)
        tracemalloc.start()
        timeline = tank.inputs(cfg, {"action": "pause", "k": 100})
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        self.assertLess(peak, 100_000)  # materialized series cost megabytes on every plant advance
        self.assertEqual((len(timeline.Ff), timeline.Ff[99], timeline.Ff[100]), (259_200, 15.0, 4.5))
        self.assertEqual((timeline.Ph[359], timeline.Ph[360], timeline.spT[259_199]), (6.0, -0.75, 78.0))
        with self.assertRaises(IndexError):
            timeline.Ph[259_200]

    def test_inactive_events_do_not_require_unused_settings(self):
        cfg = dict(CONFIG, horizon_minutes=1, steam_drop={"at_minute": 1})
        del cfg["pause_feed_scale"]
        timeline = tank.inputs(cfg, None)
        self.assertEqual((timeline.Ff[0], timeline.Ph[0]), (cfg["feed"], 6.0))

    def test_norvale_catalog_registers_the_plant_on_a_shifted_clock(self):
        with TemporaryDirectory() as directory:
            engine = open_catalog(Path(directory), {"seed": 1, "tank": CONFIG}, {"day": 0})
            origin = engine.state[tank.COMPONENT]["origin"]
            self.assertEqual(origin, 720)  # the recurring controller opens at 12:00
            engine.run(until=origin + 60)
            self.assertEqual(engine.state[tank.COMPONENT]["plant"]["k"], 60 * tank.SCANS)
            self.assertEqual(open_catalog(Path(directory), {"seed": 1, "tank": CONFIG}, {"day": 0}).now, origin + 60)


if __name__ == "__main__":
    unittest.main()
