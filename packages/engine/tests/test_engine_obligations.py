"""Offline handler contract and recovery tests for employee obligations."""

import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from engine import DAY, DeferCompletion
from enterpriseengine import Engine
from test_engine_resume_shifts import Consumer, Native, PowerLoss, open_engine

OPERATOR = {"roles": {"operator": {}}}


class EmployeeObligations(unittest.TestCase):
    def test_role_scope_and_native_evidence(self):
        native = Native()
        native.rows["native-1"] = {"kind": "receipt", "at": 0, "roles": ["operator"]}
        native.rows["private"] = {"kind": "receipt", "at": 0, "roles": ["auditor"]}
        proof = {"system": "erp", "native_id": "native-1"}
        restricted = {"system": "erp", "native_id": "private"}

        def handle(run, kind, payload):
            if kind == "open":
                run.state["id"] = run.open_obligation("case", owner="operator", due=9, evidence={"ticket": ["A"]})
                snapshot = run.obligations()
                snapshot[0]["evidence"]["ticket"].append("forged")
                self.assertEqual(run.obligations()[0]["evidence"], {"ticket": ["A"]})
            elif kind == "intrude":
                # The mutable event descriptor cannot confer the scheduled role.
                run.event["role"] = "operator"
                self.assertEqual(run.obligations(), [])
                with self.assertRaises(PermissionError):
                    run.open_obligation("new-case", owner="operator", due=9, evidence={})
                with self.assertRaises(PermissionError):
                    run.handoff_obligation(run.state["id"], owner="auditor", due=3)
                with self.assertRaises(PermissionError):
                    run.resolve_obligation(run.state["id"], evidence=proof)
                run.confirm("erp", "receipt", "private", {"at": 0})
            elif kind == "close":
                # An existing native row must first be confirmed by the engine.
                for reference in (proof, restricted):
                    with self.assertRaisesRegex(ValueError, "confirmed native evidence"):
                        run.resolve_obligation(run.state["id"], evidence=reference)
                with self.assertRaisesRegex(ValueError, "system and native_id"):
                    run.resolve_obligation(run.state["id"], evidence={"approved": True})
                run.confirm("erp", "receipt", "native-1", {"at": 0})
                run.resolve_obligation(run.state["id"], evidence=proof)
                run.resolve_obligation(run.state["id"], evidence=proof)
                run.open_obligation("case", owner="operator", due=9, evidence={"ticket": ["A"]})
                self.assertEqual(run.obligations(), [])
            else:
                self.fail(f"Unexpected dispatch: {kind}")

        with TemporaryDirectory() as directory:
            run = open_engine(directory, Consumer({"roles": {"operator": {}, "auditor": {}}}, handle), native=native)
            with self.assertRaises(PermissionError):
                run.obligations()
            with self.assertRaises(PermissionError):
                run.open_obligation("outside", owner="operator", due=0, evidence={})
            for at, (kind, role) in enumerate([("open", "operator"), ("intrude", "auditor"), ("close", "operator")]):
                run.schedule(kind, kind, at, payload={}, role=role)
            run.run(until=9)
            self.assertEqual(run.now, 2)
            self.assertEqual(native.sends, 0)

    def test_duplicate_deadlines_and_shift_handoff_survive_restart(self):
        config = {"shifts": {"operator": [(1, 5)], "relief": [(10, 15)]}}
        reminders = []

        def handle(run, kind, payload):
            if kind == "open":
                for _ in range(2):
                    run.state["id"] = run.open_obligation("case", owner="operator", due=4, evidence="ticket-A")
                with self.assertRaisesRegex(ValueError, "different terms"):
                    run.open_obligation("case", owner="operator", due=3, evidence="ticket-A")
            elif kind == "handoff":
                run.handoff_obligation(run.state["id"], owner="operator", due=4)
                run.handoff_obligation(run.state["id"], owner="operator", due=12)
                # Move the deadline back through the original minute. Its original edge is stale.
                run.handoff_obligation(run.state["id"], owner="operator", due=4)
                run.handoff_obligation(run.state["id"], owner="relief", due=6)
                self.assertEqual(run.obligations(), [])
            else:
                self.assertEqual(kind, "obligation_due")
                self.assertEqual(run.event["kind"], kind)
                self.assertEqual(payload, {"id": run.state["id"], "key": "case"})
                self.assertEqual(
                    run.obligations(),
                    [{"id": run.state["id"], "key": "case", "owner": "relief", "due": 6, "evidence": "ticket-A"}],
                )
                run.handoff_obligation(run.state["id"], owner="relief", due=6)
                reminders.append((run.now, run.event["role"]))

        with TemporaryDirectory() as directory:
            consumer = Consumer(config, handle)
            run = open_engine(directory, consumer)
            run.schedule("open", "open", 1, payload={}, role="operator")
            run.schedule("handoff", "handoff", 2, payload={}, role="operator")
            for minute in (1, 2, 9, 10, DAY + 15):
                run = open_engine(directory, consumer)
                run.run(until=minute)
                self.assertEqual(reminders, [] if minute < 10 else [(10, "relief")])
            phases = [entry["phase"] for entry in run.history() if entry["id"].startswith("obligation:")]
            self.assertEqual(phases, ["start", "complete"])

    def test_interrupted_open_and_resolution_replay_atomically(self):
        native = Native()
        attempts = []

        def handle(run, kind, payload):
            attempts.append((kind, run.event["sequence"]))
            if kind == "open":
                run.state["id"] = run.open_obligation("case", owner="operator", due=3, evidence="ticket-B")
                if len(attempts) == 1:
                    raise PowerLoss()
            else:
                self.assertEqual(kind, "obligation_due")
                self.assertEqual([row["key"] for row in run.obligations()], ["case"])
                if len(attempts) == 4:
                    exported = run.export()["records"][0]
                    with self.assertRaisesRegex(ValueError, "confirmed native evidence"):
                        run.resolve_obligation(
                            payload["id"], evidence={"system": exported["system"], "native_id": exported["native_id"]}
                        )
                record = run.write("erp", "receipt", {"case": payload["key"]})
                proof = {key: record[key] for key in ("system", "native_id")}
                run.resolve_obligation(run.state["id"], evidence=proof)
                self.assertEqual(run.obligations(), [])
                if len(attempts) == 3:
                    raise PowerLoss()
                run.state["completed"] = run.now

        with TemporaryDirectory() as directory:
            consumer = Consumer(OPERATOR, handle)
            run = open_engine(directory, consumer, native=native)
            run.schedule("open", "open", 0, payload={}, role="operator")
            with self.assertRaises(PowerLoss):
                run.run(until=0)
            self.assertFalse(run.has_event("obligation:1"))
            run = open_engine(directory, consumer, native=native)
            with self.assertRaises(PowerLoss):
                run.run(until=3)
            audit = run.private_truth()["obligation_history"][run.state["id"]]["entries"]
            self.assertEqual([entry["action"] for entry in audit], ["open"])
            run = open_engine(directory, consumer, native=native)
            run.run(until=DAY)
            self.assertEqual(attempts, [("open", 0)] * 2 + [("obligation_due", 1)] * 2)
            self.assertEqual(native.sends, 1)
            self.assertEqual(run.state["completed"], 3)
            run = open_engine(directory, consumer, native=native)
            run.run(until=2 * DAY)
            self.assertEqual(len(attempts), 4)
            self.assertEqual(len(run.history()), 4)

    def test_superseded_deferral_preserves_native_truth_and_terminal_evidence(self):
        native = Native()
        alarm_calls = []

        def handle(run, kind, payload):
            if kind == "open":
                run.state["id"] = run.open_obligation(
                    "incident", owner="operator", due=1, evidence={"private": "absence context"}
                )
            elif kind in ("obligation_due", "unrelated"):
                if kind == "obligation_due":
                    alarm_calls.append(run.event["id"])
                else:
                    run.event["id"] = "open"
                record = run.write("tickets", kind, {})
                run.state["rolled_back"] = record["native_id"]
                raise DeferCompletion()
            elif kind == "handoff":
                run.handoff_obligation(run.state["id"], owner="relief", due=10)
            elif kind == "resolve":
                records = {record["kind"]: record for record in run.export()["records"]}

                def reference(kind):
                    return {"system": "tickets", "native_id": records[kind]["native_id"]}

                with self.assertRaisesRegex(ValueError, "confirmed native evidence"):
                    run.resolve_obligation(run.state["id"], evidence=reference("unrelated"))
                proof = reference("obligation_due")
                run.resolve_obligation(run.state["id"], evidence=proof)
                run.resolve_obligation(run.state["id"], evidence=proof)
                with self.assertRaises(PermissionError):
                    run.private_truth()
            else:
                self.fail(f"Unexpected dispatch: {kind}")

        class Grader(Consumer):
            def grade_extraction(self, records, cutoff):
                return [record["kind"] for record in records], {}

        consumer = Grader({"roles": {"operator": {}, "relief": {}}}, handle)
        with TemporaryDirectory() as directory:
            run = open_engine(directory, consumer, native=native)
            for at, kind, role in [
                (0, "open", "operator"),
                (2, "unrelated", "operator"),
                (3, "handoff", "operator"),
                (4, "resolve", "relief"),
            ]:
                run.schedule(kind, kind, at, payload={}, role=role)
            run.run(until=2)
            self.assertEqual(len(run.export()["records"]), 2)
            self.assertEqual(run.extractor_questions(), [])
            self.assertNotIn("rolled_back", run.state)
            run = open_engine(directory, consumer, native=native)
            run.run(until=10)
            run = open_engine(directory, consumer, native=native)
            self.assertEqual(
                [row for row in run.history() if row["id"] == alarm_calls[0]],
                [
                    {"id": alarm_calls[0], "phase": "start", "at": 1},
                    {"id": alarm_calls[0], "phase": "superseded", "at": 3},
                ],
            )
            self.assertEqual(run.extractor_questions(as_of=2), [])
            self.assertEqual(run.extractor_questions(as_of=3), ["obligation_due"])
            self.assertEqual(run.extractor_questions(), ["obligation_due"])
            self.assertEqual(len(run.export()["records"]), 2)
            self.assertEqual(native.sends, 2)
            self.assertEqual(len(alarm_calls), 1)
            obligation_id = run.state["id"]
            audit = run.private_truth()["obligation_history"][obligation_id]["entries"]
            self.assertEqual([entry["action"] for entry in audit], ["open", "handoff", "resolve"])
            self.assertEqual(audit[0]["evidence"], {"private": "absence context"})
            self.assertEqual((audit[1]["role"], audit[1]["owner"]), ("operator", "relief"))
            self.assertEqual(audit[2]["role"], "relief")
            self.assertEqual(audit[2]["event"], "resolve")
            self.assertEqual(audit[2]["evidence_event"], alarm_calls[0])
            self.assertEqual(audit[2]["operation"], audit[2]["evidence"]["native_id"])
            historical = run.private_truth(as_of=3)["obligation_history"][obligation_id]["entries"]
            self.assertEqual(historical, audit[:2])
            for public in (run.export(), run.history(), run.extractor_questions(), run.observe("relief", as_of=4)):
                self.assertNotIn("absence context", json.dumps(public))
                self.assertNotIn(obligation_id, json.dumps(public))

    def test_same_short_key_is_independent_across_origins_and_after_transfer(self):
        alarms = []
        config = {"roles": {"alpha": {}, "beta": {}}}

        def handle(run, kind, payload):
            role = run.event["role"]
            ids = run.state.setdefault("ids", {})
            if kind == "open":
                if role == "beta":
                    self.assertEqual(run.obligations(), [])
                    with self.assertRaises(PermissionError):
                        run.handoff_obligation(ids["alpha"], owner=role, due=6)
                ids[role] = run.open_obligation("same", owner=role, due=6, evidence=role)
            elif kind == "transfer":
                run.handoff_obligation(ids["alpha"], owner="beta", due=5)
            elif kind == "reopen":
                self.assertEqual(run.open_obligation("same", owner="alpha", due=6, evidence="alpha"), ids["alpha"])
                self.assertEqual(run.obligations(), [])
                with self.assertRaises(PermissionError):
                    run.resolve_obligation(ids["alpha"], evidence={})
            else:
                self.assertEqual(kind, "obligation_due")
                self.assertEqual(role, "beta")
                self.assertEqual(payload["key"], "same")
                self.assertEqual({row["id"] for row in run.obligations()}, set(ids.values()))
                with self.assertRaises(PermissionError):
                    run.handoff_obligation("same", owner=role, due=99)
                alarms.append((run.now, payload["id"]))

        with TemporaryDirectory() as directory:
            consumer = Consumer(config, handle)
            run = open_engine(directory, consumer)
            for at, kind, role in [
                (0, "open", "alpha"),
                (1, "open", "beta"),
                (2, "transfer", "alpha"),
                (3, "reopen", "alpha"),
            ]:
                run.schedule(f"{kind}:{role}", kind, at, role=role, payload={})
            run.run(until=2)
            run = open_engine(directory, consumer)
            run.run(until=2 * DAY)
            self.assertEqual(alarms, [(5, run.state["ids"]["alpha"]), (6, run.state["ids"]["beta"])])

    def test_roster_and_scoped_manager_reassignment_keep_priority_and_due(self):
        native = Native()
        config = {
            "roles": {"worker": {}, "other_worker": {}, "manager": {"permissions": ["obligation:reassign:worker"]}},
            "shifts": {"replacement": [[1, 11]]},
        }

        def handle(run, kind, payload):
            if kind == "open":
                run.state["case"] = run.open_obligation("incident", owner="worker", due=5, evidence={}, priority=1)
                with self.assertRaisesRegex(ValueError, "registered role"):
                    run.handoff_obligation(run.state["case"], owner="missing", due=4)
                self.assertEqual(run.obligations()[0]["due"], 5)
            elif kind == "other":
                run.state["other"] = run.open_obligation("other", owner="other_worker", due=100, evidence={})
            elif kind == "unprivileged":
                with self.assertRaises(PermissionError):
                    run.reassign_obligation(run.state["case"], owner="replacement", due=5)
            elif kind == "legacy":
                self.assertEqual(run.event["role"], None)
                with self.assertRaises(PermissionError):
                    run.obligations()
            elif kind == "manager":
                run.event["role"] = "worker"
                self.assertEqual(run.obligations(), [])
                with self.assertRaises(PermissionError):
                    run.reassign_obligation(run.state["other"], owner="replacement", due=5)
                with self.assertRaises(PermissionError):
                    run.resolve_obligation(run.state["case"], evidence={})
                with self.assertRaisesRegex(ValueError, "registered role"):
                    run.reassign_obligation(run.state["case"], owner="missing", due=5)
                run.reassign_obligation(run.state["case"], owner="replacement", due=5)
                with self.assertRaises(PermissionError):
                    run.reassign_obligation(run.state["case"], owner="worker", due=5)
                run.schedule("response", "response", 5, role="replacement", payload={})
            elif kind == "response":
                self.assertEqual(run.obligations()[0]["due"], 5)
                record = run.write("tickets", "ticket_response", {})
                run.resolve_obligation(
                    run.state["case"], evidence={"system": record["system"], "native_id": record["native_id"]}
                )
            else:
                self.fail(f"Unexpected dispatch: {kind}")

        with TemporaryDirectory() as directory:
            consumer = Consumer(config, handle)
            run = open_engine(directory, consumer, native=native)
            for at, kind, role in [
                (0, "open", "worker"),
                (0, "other", "other_worker"),
                (1, "legacy", None),
                (1, "unprivileged", "other_worker"),
                (2, "manager", "manager"),
            ]:
                run.schedule(kind, kind, at, role=role, payload={})
            run.run(until=2)
            run = open_engine(directory, consumer, native=native)
            run.run(until=6)
            self.assertEqual(native.sends, 1)
            self.assertEqual(run.history()[-1], {"id": "response", "phase": "complete", "at": 5})
            audit = run.private_truth()["obligation_history"][run.state["case"]]["entries"]
            self.assertEqual([entry["action"] for entry in audit], ["open", "reassign", "resolve"])
            self.assertEqual(
                audit[1],
                {
                    "action": "reassign",
                    "at": 2,
                    "event": "manager",
                    "role": "manager",
                    "previous_due": 5,
                    "due": 5,
                    "previous_owner": "worker",
                    "owner": "replacement",
                },
            )
            self.assertEqual(audit[2]["role"], "replacement")

    def test_deferral_retains_deadline_identity_and_legacy_dispatch(self):
        calls = []

        def handle(run, kind, payload):
            if kind == "open":
                run.state["id"] = run.open_obligation("case", owner="operator", due=0, evidence={})
            elif payload.get("id") == run.state.get("id") and "id" in payload:
                calls.append((run.now, run.event["sequence"]))
                if run.now < DAY:
                    raise DeferCompletion()
            else:
                self.assertEqual(kind, "obligation_due")
                self.assertEqual(payload, {"older": True})

        with TemporaryDirectory() as directory:
            consumer = Consumer(OPERATOR, handle)
            run = open_engine(directory, consumer)
            run.schedule("legacy", "obligation_due", 3, payload={"older": True})
            run.schedule("open", "open", 1, payload={}, role="operator")
            run.run(until=3)
            run = open_engine(directory, consumer)
            run.run(until=DAY + 1)
            self.assertEqual(calls, [(1, 2), (DAY + 1, 2)])

    def test_evidence_obeys_system_scope_and_current_handler_readback(self):
        native = Native()
        native.rows["native-1"] = {"kind": "receipt", "at": 0}
        proof = {"system": "desk", "native_id": "native-1"}

        def handle(run, kind, payload):
            if kind == "confirm":
                run.confirm("desk", "receipt", "native-1", {"at": 0})
            else:
                run.state["id"] = run.open_obligation("case", owner="employee", due=5, evidence={})
                with self.assertRaisesRegex(ValueError, "confirmed native evidence"):
                    run.resolve_obligation(run.state["id"], evidence=proof)
                run.confirm("desk", "receipt", "native-1", {"at": 0})
                run.resolve_obligation(run.state["id"], evidence=proof)

        with TemporaryDirectory() as directory:
            run = Engine(
                directory,
                Consumer(OPERATOR, handle),
                seed=41,
                native=None,
                providers=None,
                monotonic=monotonic,
                adapters={"fixture": lambda system, spec: native},
            )
            run.extend(
                "roles",
                systems={"desk": {"adapter": "fixture"}},
                roles={
                    role: {"shifts": [[0, DAY]], "permissions": ["write:desk"]} for role in ("reviewer", "employee")
                },
            )
            run.schedule("confirm", "confirm", 0, payload={}, role="reviewer")
            run.schedule("close", "close", 1, payload={}, role="employee")
            run.run(until=5)
            self.assertEqual(run.now, 1)

    def test_global_key_checkpoint_and_generated_alarm_migrate_on_reopen(self):
        def handle(run, kind, payload):
            if kind == "open":
                run.open_obligation("case", owner="operator", due=3, evidence="legacy")
            else:
                self.assertEqual(kind, "obligation_due")
                self.assertEqual(payload["key"], "case")
                self.assertEqual(run.obligations()[0]["id"], payload["id"])
                self.assertEqual(run.open_obligation("case", owner="operator", due=3, evidence="legacy"), payload["id"])
                run.state["legacy_id"] = payload["id"]

        with TemporaryDirectory() as directory:
            consumer = Consumer(OPERATOR, handle)
            run = open_engine(directory, consumer)
            run.schedule("open", "open", 0, payload={}, role="operator")
            run.run(until=0)
            with closing(sqlite3.connect(Path(directory) / "engine.sqlite3")) as db, db:
                checkpoint = json.loads(db.execute("SELECT data FROM checkpoint").fetchone()[0])
                obligation = next(iter(checkpoint["obligations"].values()))
                checkpoint["obligations"] = {
                    "case": {
                        key: obligation[key] for key in ("key", "owner", "due", "evidence", "resolution", "reminder")
                    }
                }
                db.execute("UPDATE checkpoint SET data = ?", (json.dumps(checkpoint),))
                edge = json.loads(db.execute("SELECT edge FROM queue").fetchone()[0])
                edge[6] = {"key": "case"}
                edge[10] = True
                db.execute("UPDATE queue SET edge = ?", (json.dumps(edge),))
            run = open_engine(directory, consumer)
            run.run(until=3)
            run = open_engine(directory, consumer)
            run.run(until=2 * DAY)
            self.assertIn("legacy_id", run.state)
            self.assertEqual([row["phase"] for row in run.history()], ["start", "complete"] * 2)
