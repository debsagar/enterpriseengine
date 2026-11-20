"""Offline handler contract and recovery tests for employee obligations."""

from tempfile import TemporaryDirectory
from time import monotonic
import unittest

from enterpriseengine import Engine
from engine import DAY, DeferCompletion
from test_engine_resume_shifts import Consumer, Native, PowerLoss, open_engine


class EmployeeObligations(unittest.TestCase):
    def test_role_scope_and_native_evidence(self):
        native = Native()
        native.rows["native-1"] = {"kind": "receipt", "at": 0, "roles": ["operator"]}
        native.rows["private"] = {"kind": "receipt", "at": 0, "roles": ["auditor"]}
        proof = {"system": "erp", "native_id": "native-1"}
        restricted = {"system": "erp", "native_id": "private"}

        def handle(run, kind, payload):
            if kind == "open":
                run.open_obligation("case", owner="operator", due=9, evidence={"ticket": ["A"]})
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
                    run.handoff_obligation("case", owner="auditor", due=3)
                with self.assertRaises(PermissionError):
                    run.resolve_obligation("case", evidence=proof)
                run.confirm("erp", "receipt", "private", {"at": 0})
            elif kind == "close":
                # An existing native row must first be confirmed by the engine.
                for reference in (proof, restricted):
                    with self.assertRaisesRegex(ValueError, "confirmed native evidence"):
                        run.resolve_obligation("case", evidence=reference)
                with self.assertRaisesRegex(ValueError, "system and native_id"):
                    run.resolve_obligation("case", evidence={"approved": True})
                run.confirm("erp", "receipt", "native-1", {"at": 0})
                run.resolve_obligation("case", evidence=proof)
                run.resolve_obligation("case", evidence=proof)
                run.open_obligation("case", owner="operator", due=9, evidence={"ticket": ["A"]})
                self.assertEqual(run.obligations(), [])
            else:
                self.fail(f"Unexpected dispatch: {kind}")

        with TemporaryDirectory() as directory:
            run = open_engine(directory, Consumer(handle=handle), native=native)
            with self.assertRaises(PermissionError):
                run.obligations()
            with self.assertRaises(PermissionError):
                run.open_obligation("outside", owner="operator", due=0, evidence={})
            for at, (kind, role) in enumerate(
                [("open", "operator"), ("intrude", "auditor"), ("close", "operator")]
            ):
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
                    run.open_obligation("case", owner="operator", due=4, evidence="ticket-A")
                with self.assertRaisesRegex(ValueError, "different terms"):
                    run.open_obligation("case", owner="operator", due=3, evidence="ticket-A")
            elif kind == "handoff":
                run.handoff_obligation("case", owner="operator", due=4)
                run.handoff_obligation("case", owner="operator", due=12)
                # Move the deadline back through the original minute. Its original edge is stale.
                run.handoff_obligation("case", owner="operator", due=4)
                run.handoff_obligation("case", owner="relief", due=6)
                self.assertEqual(run.obligations(), [])
            else:
                self.assertEqual(kind, "obligation_due")
                self.assertEqual(run.event["kind"], kind)
                self.assertEqual(payload, {"key": "case"})
                self.assertEqual(run.obligations(), [
                    {"key": "case", "owner": "relief", "due": 6, "evidence": "ticket-A"}
                ])
                run.handoff_obligation("case", owner="relief", due=6)
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
                run.open_obligation("case", owner="operator", due=3, evidence="ticket-B")
                if len(attempts) == 1:
                    raise PowerLoss()
            else:
                self.assertEqual(kind, "obligation_due")
                self.assertEqual([row["key"] for row in run.obligations()], ["case"])
                record = run.write("erp", "receipt", {"case": payload["key"]})
                proof = {key: record[key] for key in ("system", "native_id")}
                run.resolve_obligation("case", evidence=proof)
                self.assertEqual(run.obligations(), [])
                if len(attempts) == 3:
                    raise PowerLoss()
                run.state["completed"] = run.now

        with TemporaryDirectory() as directory:
            consumer = Consumer(handle=handle)
            run = open_engine(directory, consumer, native=native)
            run.schedule("open", "open", 0, payload={}, role="operator")
            with self.assertRaises(PowerLoss):
                run.run(until=0)
            self.assertFalse(run.has_event("obligation:1"))
            run = open_engine(directory, consumer, native=native)
            with self.assertRaises(PowerLoss):
                run.run(until=3)
            run = open_engine(directory, consumer, native=native)
            run.run(until=DAY)
            self.assertEqual(attempts, [("open", 0)] * 2 + [("obligation_due", 1)] * 2)
            self.assertEqual(native.sends, 1)
            self.assertEqual(run.state, {"completed": 3})
            run = open_engine(directory, consumer, native=native)
            run.run(until=2 * DAY)
            self.assertEqual(len(attempts), 4)
            self.assertEqual(len(run.history()), 4)

    def test_deferral_retains_deadline_identity_and_legacy_dispatch(self):
        calls = []

        def handle(run, kind, payload):
            if kind == "open":
                run.open_obligation("case", owner="operator", due=0, evidence={})
            elif payload == {"key": "case"}:
                calls.append((run.now, run.event["sequence"]))
                if run.now < DAY:
                    raise DeferCompletion()
            else:
                self.assertEqual(kind, "obligation_due")
                self.assertEqual(payload, {"older": True})

        with TemporaryDirectory() as directory:
            consumer = Consumer(handle=handle)
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
                run.open_obligation("case", owner="employee", due=5, evidence={})
                with self.assertRaisesRegex(ValueError, "confirmed native evidence"):
                    run.resolve_obligation("case", evidence=proof)
                run.confirm("desk", "receipt", "native-1", {"at": 0})
                run.resolve_obligation("case", evidence=proof)

        with TemporaryDirectory() as directory:
            run = Engine(directory, Consumer(handle=handle), seed=41, native=None, providers=None,
                         monotonic=monotonic, adapters={"fixture": lambda system, spec: native})
            run.extend("roles", systems={"desk": {"adapter": "fixture"}},
                       roles={role: {"shifts": [[0, DAY]], "permissions": ["write:desk"]}
                              for role in ("reviewer", "employee")})
            run.schedule("confirm", "confirm", 0, payload={}, role="reviewer")
            run.schedule("close", "close", 1, payload={}, role="employee")
            run.run(until=5)
            self.assertEqual(run.now, 1)
