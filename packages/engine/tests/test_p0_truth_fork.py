"""P0 gate: widened private truth up to a cutoff and fork snapshot-hook round trip."""

import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from engine import Engine

# Each event writes one record and links it to earlier records by native ID.
FLOW = (
    # (event id, kind, system, minute, duration, link fields)
    ("order", "order", "erp", 1, 0, {}),
    ("batch", "batch", "erp", 3, 0, {"order_id": "order"}),
    ("inspection", "inspection", "qms", 5, 0, {"batch_id": "batch"}),
    ("shipment", "shipment", "logistics", 8, 0, {"batch_id": "batch", "order_id": "order"}),
    # Starts before the cutoff but only completes at minute 16.
    ("invoice", "invoice", "erp", 6, 10, {"shipment_id": "shipment"}),
    ("late", "note", "erp", 12, 0, {"order_id": "order"}),
)


class Consumer:
    config = {}

    def handle(self, run, kind, payload):
        event_id = run.event["id"]
        run.save_consumer_decision(event_id, {"input": payload, "choice": f"record {kind}"})
        ids = run.state.setdefault("flow", {"ids": {}})["ids"]
        fields = {"case": "C1", "quantity": payload["quantity"]}
        fields |= {field: ids[source] for field, source in payload["links"].items()}
        record = run.write(payload["system"], kind, fields)
        ids[event_id] = record["native_id"]


class Native:
    """File-backed external system so a fork must snapshot it explicitly."""

    def __init__(self, directory):
        self.database = Path(directory) / "external.sqlite3"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, retry TEXT UNIQUE, data TEXT)")

    def send(self, system, retry_id, fields):
        native_id = f"{system.upper()}-{retry_id}"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO records VALUES (?, ?, ?)", (native_id, retry_id, json.dumps(fields)))
        return native_id

    def find(self, system, retry_id):
        with closing(sqlite3.connect(self.database)) as db:
            row = db.execute("SELECT id FROM records WHERE retry = ?", (retry_id,)).fetchone()
        return row and row[0]

    def read(self, system, native_id):
        with closing(sqlite3.connect(self.database)) as db:
            return json.loads(db.execute("SELECT data FROM records WHERE id = ?", (native_id,)).fetchone()[0])


def open_engine(directory):
    return Engine(directory, Consumer(), seed=5, native=Native(directory), providers=None, monotonic=monotonic)


def schedule_flow(run):
    for index, (event_id, kind, system, at, duration, links) in enumerate(FLOW, 1):
        payload = {"system": system, "quantity": index, "links": links}
        run.schedule(event_id, kind, at, duration=duration, payload=payload)


def snapshot_native(staging_dir, minute, source_dir, minutes):
    minutes.append(minute)
    with (
        closing(sqlite3.connect(Path(source_dir) / "external.sqlite3")) as original,
        closing(sqlite3.connect(Path(staging_dir) / "external.sqlite3")) as target,
    ):
        original.backup(target)


def kinds(truth):
    return [entity["kind"] for entity in truth["entities"]]


def link_kinds(truth):
    by_ref = {(e["system"], e["native_id"]): e["kind"] for e in truth["entities"]}
    return sorted((by_ref[tuple(link["from"])], link["field"], by_ref[tuple(link["to"])]) for link in truth["links"])


class P0Truth(unittest.TestCase):
    def test_truth_lists_entities_links_and_decisions_without_future_events(self):
        with TemporaryDirectory() as directory:
            run = open_engine(directory)
            schedule_flow(run)
            run.run(until=20, advance=True)

            full = run.private_truth()
            self.assertEqual(kinds(full), ["order", "batch", "inspection", "shipment", "note", "invoice"])
            self.assertEqual(
                link_kinds(full),
                [
                    ("batch", "order_id", "order"),
                    ("inspection", "batch_id", "batch"),
                    ("invoice", "shipment_id", "shipment"),
                    ("note", "order_id", "order"),
                    ("shipment", "batch_id", "batch"),
                    ("shipment", "order_id", "order"),
                ],
            )
            order = full["entities"][0]
            self.assertEqual(order["system"], "erp")
            self.assertEqual(order["event_id"], "order")
            self.assertEqual(order["fields"], {"case": "C1", "quantity": 1})
            self.assertEqual(len(full["cases"]["C1"]), 6)
            self.assertEqual(
                [(d["event_id"], d["completed_at"]) for d in full["decisions"]],
                [("order", 1), ("batch", 3), ("inspection", 5), ("shipment", 8), ("invoice", 16), ("late", 12)],
            )
            self.assertEqual(full["decisions"][0]["decision"]["choice"], "record order")
            # Existing keys survive the widening.
            self.assertEqual(full["world"], [])
            self.assertEqual(full["answers"], {})

            # Minute 8: the invoice is in flight and the note is still scheduled.
            at_8 = run.private_truth(as_of=8)
            self.assertEqual(kinds(at_8), ["order", "batch", "inspection", "shipment"])
            self.assertEqual(len(at_8["links"]), 4)
            self.assertEqual([d["event_id"] for d in at_8["decisions"]], ["order", "batch", "inspection", "shipment"])
            self.assertEqual(len(at_8["cases"]["C1"]), 4)
            for truth, cutoff in ((at_8, 8), (run.private_truth(as_of=4), 4), (run.private_truth(as_of=0), 0)):
                self.assertTrue(all(entity["at"] <= cutoff for entity in truth["entities"]))
                self.assertTrue(all(decision["completed_at"] <= cutoff for decision in truth["decisions"]))
                text = json.dumps(truth)
                for future in ('"invoice"', '"note"', '"late"'):
                    self.assertNotIn(future, text)

            at_4 = run.private_truth(as_of=4)
            self.assertEqual(link_kinds(at_4), [("batch", "order_id", "order")])
            self.assertEqual(
                run.private_truth(as_of=0), {**full, "entities": [], "links": [], "cases": {}, "decisions": []}
            )

    def test_world_rows_are_unchanged_by_widening(self):
        with TemporaryDirectory() as directory:
            consumer = Consumer()
            consumer.config = {"world": True, "private_marker": "hidden"}
            run = Engine(directory, consumer, seed=5, native=Native(directory), providers=None, monotonic=monotonic)
            run.run(until=7 * 1440)
            truth = run.private_truth()
            self.assertEqual([row["at"] for row in truth["world"]], [0, 7 * 1440])
            self.assertEqual(truth["marker"], "hidden")
            self.assertEqual(kinds(truth), ["world_bulletin", "world_bulletin"])
            self.assertEqual(len(truth["world"]), len(run.private_truth(as_of=7 * 1440)["world"]))
            self.assertEqual(len(run.private_truth(as_of=7 * 1440 - 1)["world"]), 1)


class P0Fork(unittest.TestCase):
    def test_fork_round_trips_state_truth_and_external_snapshot(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            source = open_engine(source_dir)
            schedule_flow(source)
            source.run(until=10, advance=True)
            minutes = []
            branch_dir = source.fork(
                root / "branch",
                snapshot_external=lambda path, minute: snapshot_native(path, minute, source_dir, minutes),
            )
            self.assertEqual(minutes, [10])
            self.assertEqual(
                sorted(p.name for p in branch_dir.iterdir() if p.suffix == ".sqlite3"),
                ["engine.sqlite3", "external.sqlite3"],
            )

            branch = open_engine(branch_dir)
            self.assertEqual(branch.now, 10)
            self.assertEqual(branch.history(), source.history())
            self.assertEqual(branch.state, source.state)
            self.assertEqual(branch.export(), source.export())
            self.assertEqual(branch.private_truth(), source.private_truth())

            # Both continue identically; the branch's external writes stay in its own copy.
            source.run(until=20, advance=True)
            branch.run(until=20, advance=True)
            self.assertEqual(branch.private_truth(), source.private_truth())
            self.assertEqual(branch.export(), source.export())
            invoice = branch.private_truth()["entities"][-1]
            self.assertEqual(invoice["kind"], "invoice")
            self.assertEqual(
                Native(branch_dir).read("erp", invoice["native_id"])["shipment_id"], invoice["fields"]["shipment_id"]
            )

    def test_hook_that_alters_checkpoint_copy_is_rejected_without_branch(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            source = open_engine(source_dir)
            schedule_flow(source)
            source.run(until=4, advance=True)

            def clobber(staging_dir, minute):
                (Path(staging_dir) / "engine.sqlite3").write_bytes(b"not a checkpoint")

            with self.assertRaisesRegex(RuntimeError, "must not alter the engine checkpoint"):
                source.fork(root / "branch", snapshot_external=clobber)
            self.assertFalse((root / "branch").exists())
            with self.assertRaisesRegex(ValueError, "snapshot hook"):
                source.fork(root / "branch")
            minutes = []
            branch = source.fork(
                root / "branch",
                snapshot_external=lambda path, minute: snapshot_native(path, minute, source_dir, minutes),
            )
            self.assertEqual(minutes, [4])
            self.assertEqual(open_engine(branch).private_truth(), source.private_truth())


if __name__ == "__main__":
    unittest.main()
