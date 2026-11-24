"""Offline acceptance for two consumers of the shared obligation API."""

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from docs.examples.obligation_cases import FixtureRecords, ManagedServicesSLA, WarehouseShortage, open_run


class Interrupted(BaseException):
    pass


class ObligationCases(unittest.TestCase):
    def test_warehouse_shortage_resumes_with_one_case_outstanding(self):
        self.exercise(WarehouseShortage(), due=26, due_keys=("shortage-B",),
                      closure_at=25, closure_suffix="final", partial_receipts=True)

    def test_restoration_sla_keeps_its_deadline_across_handoff(self):
        self.exercise(ManagedServicesSLA(), due=20, due_keys=("incident-A", "incident-B"),
                      closure_at=24, closure_suffix="verified")

    def exercise(self, desk, *, due, due_keys, closure_at, closure_suffix, partial_receipts=False):
        self.maxDiff = None
        with TemporaryDirectory() as tmp:
            resolved, outstanding = desk.keys
            directory = Path(tmp) / "run"
            closure_id = f"{resolved}-{closure_suffix}"
            rows = list(desk.native_records())
            first_receipt = next(row for row in rows if row["native_id"] == f"{resolved}-verified")
            cross_case = {**first_receipt, "native_id": "other-case", "at": 22}
            cross_source = {**first_receipt, "system": "unrelated", "case": outstanding,
                            "native_id": f"{outstanding}-partial"}
            # Put the foreign row before the same ID in the authoritative source.
            # Duplicate both the native receipt row and its delivery event.
            with patch.object(desk, "native_records",
                              return_value=[cross_source, cross_case, *rows, first_receipt]):
                run = open_run(directory, desk)
                run.extend("opening", events=desk.opening())
                extra = [
                    dict(id="wrong-case", kind="check", at=22, role=desk.followup_owner,
                         payload={"key": outstanding, "record": cross_case["native_id"]}),
                ]
                if partial_receipts:
                    extra.append(dict(id="repeat-partial", kind="check", at=24, role=desk.followup_owner,
                                      payload={"key": resolved, "record": first_receipt["native_id"]}))
                run.extend("test-deliveries", events=extra)
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
                opening_ids = [f"{key}-open" for key in desk.keys]
                self.assert_confirmed(run, opening_ids)
                case_ids = run.state["case_ids"]
                self.assertEqual(len(set(case_ids.values())), 2)
                obligations = [
                    dict(id=case_ids[key], key=key, owner=desk.followup_owner, due=due,
                         evidence={"system": desk.system, "native_id": f"{key}-open"})
                    for key in desk.keys
                ]
                self.assert_visible(facts, desk.initial_owner, 13, [])
                self.assert_visible(facts, desk.followup_owner, 13, obligations)

                run.run(until=closure_at - 1)
                self.assertEqual(self.outcome(run, "wrong-case")["kind"], "pending")
                confirmed = opening_ids.copy()
                if partial_receipts:
                    confirmed.extend([first_receipt["native_id"], f"{outstanding}-partial"])
                    self.assertEqual(self.outcome(run, f"check:{resolved}:24:{desk.followup_owner}")["kind"],
                                     "pending")
                    self.assertEqual(self.outcome(run, "repeat-partial")["kind"], "pending")
                self.assert_confirmed(run, confirmed)
                self.assertFalse(any(row["kind"] == "resolved" for row in run.state["case_history"]))

                # Lose the process after native confirmation, before the case checkpoint.
                def interrupt(stage, operation):
                    if stage == "result_persisted" and operation["record"]["native_id"] == closure_id:
                        raise Interrupted

                run = open_run(directory, desk, boundary=interrupt)
                with self.assertRaises(Interrupted):
                    run.run(until=27)
                self.assertFalse(any(row["kind"] == "resolved" for row in run.state["case_history"]))
                self.assert_confirmed(run, [*confirmed, closure_id])

                run = open_run(directory, desk)
                run.run(until=27)
                facts = run.state["case_history"]
                closure_event = f"check:{resolved}:{closure_at}:{desk.followup_owner}"
                repeat_event = f"check:{resolved}:27:{desk.followup_owner}"
                self.assertEqual(
                    [(row["key"], row["obligation_id"], row["at"], row["role"])
                     for row in facts if row["kind"] == "obligation_due"],
                    [(key, case_ids[key], due, desk.followup_owner) for key in due_keys],
                )
                self.assertEqual(
                    [row for row in facts if row["kind"] == "resolved"],
                    [dict(kind="resolved", key=resolved, at=closure_at, role=desk.followup_owner,
                          event_id=closure_event, native_id=closure_id)],
                )
                self.assertEqual(self.outcome(run, repeat_event)["kind"], "already_resolved")
                self.assertEqual(self.outcome(run, f"check:{outstanding}:24:{desk.followup_owner}")["kind"],
                                 "pending")
                self.assert_visible(facts, desk.followup_owner, 27, obligations[1:])
                self.assert_confirmed(run, [*confirmed, closure_id])
                audit = run.private_truth()["obligation_history"]
                self.assertEqual(
                    [entry["action"] for entry in audit[case_ids[outstanding]]["entries"]],
                    ["open", "handoff"],
                )
                resolved_entries = audit[case_ids[resolved]]["entries"]
                self.assertEqual([entry["action"] for entry in resolved_entries],
                                 ["open", "handoff", "resolve"])
                self.assertEqual(resolved_entries[-1]["evidence_event"], closure_event)
                self.assertEqual(
                    [row["id"] for row in run.history()
                     if row["phase"] == "complete" and row["id"] in (closure_event, repeat_event)],
                    [closure_event, repeat_event],
                )

                run = open_run(directory, desk)
                run.run(until=30)
                self.assertEqual(run.state["case_history"], facts)
                self.assert_confirmed(run, [*confirmed, closure_id])

    def test_warehouse_uses_opening_quantity_and_stock_scope(self):
        desk = WarehouseShortage()
        key = desk.keys[0]
        rows = list(desk.native_records())
        stock = dict(item="ingredient", lot="ING-5", unit="bag")
        for row in rows:
            if row["case"] == key and row["kind"] != "eta":
                row.update(stock)
                if row["kind"] == desk.opening_kind:
                    row["units_missing"] = 16
        receipt = next(row for row in rows if row["native_id"] == f"{key}-final")
        # Each of these lookalikes could independently close the shortage.
        misleading = [
            ("wrong-item", {"item": "other"}),
            ("wrong-lot", {"lot": "other"}),
            ("wrong-unit", {"unit": "each"}),
            ("held", {"available_to_pick": False}),
            ("wrong-role", {"roles": [desk.initial_owner]}),
            ("wrong-kind", {"kind": "eta"}),
        ]
        extra = []
        for suffix, fields in [*misleading, ("released", {"units_accepted": 4})]:
            native_id = f"{key}-{suffix}"
            rows.append({**receipt, "native_id": native_id, "units_accepted": 16, **fields})
            extra.append(dict(id=suffix, kind="check", at=28 if suffix == "released" else 25,
                              role=desk.followup_owner, payload={"key": key, "record": native_id}))

        with TemporaryDirectory() as tmp, patch.object(desk, "native_records", return_value=rows):
            run = open_run(tmp, desk)
            run.extend("opening", events=desk.opening())
            run.extend("test-deliveries", events=extra)
            run.run(until=27)
            for suffix, _ in misleading:
                self.assertEqual(self.outcome(run, suffix)["kind"], "pending")
            self.assertEqual(self.outcome(run, f"check:{key}:27:{desk.followup_owner}")["kind"], "pending")
            self.assertEqual(
                [row["key"] for row in run.state["case_history"] if row["kind"] == "obligation_due"],
                list(desk.keys),
            )
            self.assert_confirmed(run, [f"{key}-open", "shortage-B-open", f"{key}-verified",
                                        "shortage-B-partial", f"{key}-final"])
            run = open_run(tmp, desk)
            run.run(until=28)
            self.assertEqual(self.outcome(run, "released")["kind"], "resolved")
            self.assertEqual(run.export()["records"][-1]["native_id"], f"{key}-released")

    def test_warehouse_confirmation_requires_released_scoped_readback(self):
        desk = WarehouseShortage()
        read = FixtureRecords.read
        target = "shortage-A-final"

        for mismatch in ({"available_to_pick": False}, {"lot": "other"}, {"case": "shortage-B"}):
            with self.subTest(mismatch=mismatch), TemporaryDirectory() as tmp:
                def stale_read(transport, system, native_id):
                    row = read(transport, system, native_id)
                    return {**row, **mismatch} if native_id == target else row

                run = open_run(tmp, desk)
                run.extend("opening", events=desk.opening())
                with patch.object(FixtureRecords, "read", stale_read):
                    with self.assertRaisesRegex(RuntimeError, "Native readback mismatch"):
                        run.run(until=27)
                self.assertFalse(any(row["kind"] == "resolved" for row in run.state["case_history"]))
                self.assertNotIn(target, [row["native_id"] for row in run.export()["records"]])

                run = open_run(tmp, desk)
                run.run(until=27)
                self.assertEqual(self.outcome(run, "check:shortage-A:25:receiving")["kind"], "resolved")

    def test_published_cli_resumes_both_examples(self):
        for app, desk in [("warehouse", WarehouseShortage()), ("sla", ManagedServicesSLA())]:
            with self.subTest(app=app), TemporaryDirectory() as tmp:
                command = [sys.executable, "-m", "docs.examples.obligation_cases", app, "--run", tmp, "--until"]
                initial = json.loads(subprocess.check_output([*command, "19"]))
                final = json.loads(subprocess.check_output([*command, "27"]))
                self.assertEqual(final[:len(initial)], initial)
                resolved, outstanding = desk.keys
                self.assertEqual([row["key"] for row in final if row["kind"] == "resolved"], [resolved])
                self.assertEqual([row["key"] for row in final if row["kind"] == "already_resolved"],
                                 [resolved])
                visible = [row for row in final if row["kind"] == "inspect" and row["at"] == 27]
                self.assertEqual([row["key"] for row in visible[0]["obligations"]], [outstanding])

    def outcome(self, run, event_id):
        outcome, = [row for row in run.state["case_history"] if row.get("event_id") == event_id]
        return outcome

    def assert_confirmed(self, run, native_ids):
        self.assertEqual([row["native_id"] for row in run.export()["records"]], native_ids)

    def assert_visible(self, facts, role, at, expected):
        snapshot, = [row["obligations"] for row in facts
                     if row["kind"] == "inspect" and row["role"] == role and row["at"] == at]
        self.assertCountEqual(snapshot, expected)
