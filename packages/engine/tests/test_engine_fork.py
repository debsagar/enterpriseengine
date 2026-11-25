"""Offline proof of branching at a committed business minute."""

import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from engine import Engine


class Consumer:
    config = {}

    def handle(self, run, kind, payload):
        event_id = run.event["id"]
        run.save_consumer_decision(event_id, {"input": payload})
        run.state["plant"]["events"].append([run.now, event_id])
        run.write("external", kind, payload)


class Native:
    def __init__(self, directory):
        self.database = directory / "external.sqlite3"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, data TEXT)")

    def send(self, system, retry_id, fields):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO records VALUES (?, ?)", (retry_id, json.dumps(fields)))
        return retry_id

    def read(self, system, native_id):
        with closing(sqlite3.connect(self.database)) as db:
            return json.loads(db.execute("SELECT data FROM records WHERE id = ?", (native_id,)).fetchone()[0])


def open_engine(directory, native=None):
    return Engine(directory, Consumer(), seed=17, native=native, providers=None, monotonic=monotonic)


def database_dump(path):
    with closing(sqlite3.connect(path)) as db:
        return list(db.iterdump())


class EngineFork(unittest.TestCase):
    def test_forks_copy_committed_history_and_diverge_only_after_injection(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            native = Native(source_dir)
            source = open_engine(source_dir, native)
            initial_state = {"events": []}
            source.register_component_state("plant", initial_state)
            initial_state["events"].append("host mutation")
            source.schedule("before", "inspect", 2, payload={"value": 1})
            source.schedule("in-flight", "inspect", 5, duration=5, payload={"value": 2})
            snapshot_minutes = []

            def snapshot_external(staging_dir, minute):
                snapshot_minutes.append(minute)
                with (
                    closing(sqlite3.connect(native.database)) as original,
                    closing(sqlite3.connect(staging_dir / native.database.name)) as target,
                ):
                    original.backup(target)

            checkpoint = source_dir / "engine.sqlite3"
            with closing(sqlite3.connect(checkpoint)) as wal:
                wal.execute("PRAGMA journal_mode = WAL")
                wal.execute("BEGIN")
                wal.execute("SELECT data FROM checkpoint").fetchone()
                source.run(until=7, advance=True)
                self.assertTrue(checkpoint.with_name("engine.sqlite3-wal").is_file())
                baseline = database_dump(checkpoint)
                native_baseline = database_dump(native.database)
                left_dir = source.fork(root / "left", snapshot_external=snapshot_external)
                right_dir = source.fork(root / "right", snapshot_external=snapshot_external)

            self.assertEqual(snapshot_minutes, [7, 7])
            for branch_dir in (left_dir, right_dir):
                branch_db = branch_dir / checkpoint.name
                self.assertEqual(database_dump(branch_db), baseline)
                self.assertEqual(branch_db.stat().st_mode & 0o777, 0o600)
                self.assertEqual(database_dump(branch_dir / native.database.name), native_baseline)

            left = open_engine(left_dir, Native(left_dir))
            right = open_engine(right_dir, Native(right_dir))
            for branch in (left, right):
                self.assertEqual(branch.now, 7)
                self.assertEqual(branch.history(), source.history())
                self.assertEqual(branch.state, {"plant": {"events": [[2, "before"]]}})
                self.assertEqual(branch.export(), source.export())

            left.run(until=10)
            right.run(until=10)
            shared_history = right.history()
            shared_export = right.export()
            self.assertEqual(left.history(), shared_history)
            self.assertEqual(left.export(), shared_export)
            self.assertEqual(left.state, right.state)

            left.schedule("injected", "inspect", 11, payload={"value": 3})
            left.run(until=11)
            right.run(until=11, advance=True)
            self.assertEqual(left.history()[:-2], shared_history)
            self.assertEqual(left.history()[-1], {"id": "injected", "phase": "complete", "at": 11})
            self.assertEqual(right.history(), shared_history)
            self.assertEqual(right.export(), shared_export)
            self.assertEqual(left.export()["records"][-1]["value"], 3)
            self.assertEqual(left.state["plant"]["events"][-1], [11, "injected"])
            self.assertEqual(right.state["plant"]["events"], [[2, "before"], [10, "in-flight"]])
            self.assertFalse(right.has_event("injected"))
            self.assertEqual(database_dump(checkpoint), baseline)
            self.assertEqual(database_dump(native.database), native_baseline)

            left_native = Native(left_dir)
            self.assertEqual(left_native.read("external", "write-2-1")["value"], 3)
            with closing(sqlite3.connect(right_dir / native.database.name)) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM records").fetchone()[0], 2)
            right.schedule("different", "inspect", 12, payload={"value": 4})
            right.run(until=12)
            self.assertEqual(Native(right_dir).read("external", "write-2-1")["value"], 4)
            self.assertEqual(left_native.read("external", "write-2-1")["value"], 3)

    def test_failed_snapshot_leaves_no_branch_and_can_be_retried(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = open_engine(root / "source")
            branch = root / "branch"

            def fail(staging_dir, minute):
                (staging_dir / "partial").touch()
                raise RuntimeError("snapshot failed")

            with self.assertRaisesRegex(RuntimeError, "snapshot failed"):
                source.fork(branch, snapshot_external=fail)
            self.assertEqual(list(root.iterdir()), [root / "source"])
            self.assertEqual(source.fork(branch), branch)
            self.assertEqual(open_engine(branch).history(), [])
            with self.assertRaises(FileExistsError):
                source.fork(branch)

    def test_fork_requires_hook_for_attached_transport_and_current_checkpoint(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            source_dir.mkdir()
            source = open_engine(source_dir, Native(source_dir))
            with self.assertRaisesRegex(ValueError, "snapshot hook"):
                source.fork(root / "missing-hook")
            other = open_engine(source_dir)
            other.run(until=3, advance=True)
            with self.assertRaisesRegex(RuntimeError, "Checkpoint changed"):
                source.fork(root / "stale", snapshot_external=lambda path, minute: self.fail("stale hook called"))
            self.assertFalse((root / "stale").exists())
