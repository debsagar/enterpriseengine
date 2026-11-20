"""Offline acceptance for two consumers of the shared obligation API."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from examples.obligation_cases import ManagedServicesSLA, WarehouseShortage, open_run


class Interrupted(BaseException):
    pass


class ObligationCases(unittest.TestCase):
    def test_warehouse_shortage_resumes_with_one_case_outstanding(self):
        desk = WarehouseShortage()
        self.exercise(desk, due=26, due_keys=("shortage-B",))

    def test_restoration_sla_keeps_its_deadline_across_handoff(self):
        desk = ManagedServicesSLA()
        self.exercise(desk, due=20, due_keys=("incident-A", "incident-B"))

    def exercise(self, desk, *, due, due_keys):
        with TemporaryDirectory() as tmp:
            resolved, outstanding = desk.keys
            directory = Path(tmp) / "run"
            run = open_run(directory, desk)
            run.extend("opening", events=desk.opening())
            run.run(until=19)
            facts = run.state["case_history"]
            self.assertEqual(
                [(row["key"], row["eta"]) for row in facts if row["kind"] == "eta"],
                [(resolved, 24), (outstanding, 24)],
            )
            self.assertEqual(
                [(row["key"], row["at"]) for row in facts if row["kind"] == "pending"],
                [(resolved, 17), (outstanding, 17), (resolved, 18)],
            )
            self.assertEqual(
                [row["native_id"] for row in run.export()["records"]],
                [f"{key}-open" for key in desk.keys],
            )
            obligations = [
                dict(key=key, owner=desk.followup_owner, due=due,
                     evidence={"system": desk.system, "native_id": f"{key}-open"})
                for key in desk.keys
            ]
            self.assert_visible(facts, desk.initial_owner, 13, [])
            self.assert_visible(facts, desk.followup_owner, 13, obligations)

            # Lose the process between native readback and handler checkpoint.
            def interrupt(stage, operation):
                if stage == "result_persisted" and operation["record"]["native_id"] == f"{resolved}-verified":
                    raise Interrupted

            run = open_run(directory, desk, boundary=interrupt)
            with self.assertRaises(Interrupted):
                run.run(until=27)
            self.assertFalse(any(row["kind"] == "resolved" for row in run.state["case_history"]))

            run = open_run(directory, desk)
            run.run(until=27)
            facts = run.state["case_history"]
            self.assertEqual(
                [(row["key"], row["at"], row["role"]) for row in facts if row["kind"] == "obligation_due"],
                [(key, due, desk.followup_owner) for key in due_keys],
            )
            self.assertEqual(
                [row for row in facts if row["kind"] == "resolved"],
                [dict(kind="resolved", key=resolved, at=24, role=desk.followup_owner,
                      native_id=f"{resolved}-verified")],
            )
            self.assertIn(dict(kind="pending", key=outstanding, at=24, role=desk.followup_owner), facts)
            self.assert_visible(facts, desk.followup_owner, 27, obligations[1:])
            self.assertEqual(
                [row["native_id"] for row in run.export()["records"]],
                [*(f"{key}-open" for key in desk.keys), f"{resolved}-verified"],
            )

            run = open_run(directory, desk)
            run.run(until=30)
            self.assertEqual(run.state["case_history"], facts)

    def assert_visible(self, facts, role, at, expected):
        snapshot, = [row["obligations"] for row in facts
                     if row["kind"] == "inspect" and row["role"] == role and row["at"] == at]
        self.assertCountEqual(snapshot, expected)
