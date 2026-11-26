"""Daily behaviour numbers come from saved role decisions (R18)."""

import json
import tempfile
import unittest
from pathlib import Path

from environments.norvale.native import behaviour

FLAGGED = {"flags": [{"flag": "stock_at_or_above_requested_level"}]}


def decision(action, date="2027-01-02", facts=None, mode="model", **output):
    sent = {"role": "SCM_MGR", "as_of": date, "evidence": {}}
    if facts is not None:
        sent["facts"] = facts
    return {"mode": mode, "input": sent, "output": {"action": action, **output}}


class BehaviourReport(unittest.TestCase):
    def test_daily_numbers_by_case_type_policy_and_override(self):
        decisions = {
            "po-review:a:1": decision("approve", facts={"flags": []}, standing_policy={"approval_limit": 1}),
            "po-review:b:1": decision("request_info", facts=FLAGGED, held_from="approve", policy_rejected="dates"),
            "change-review:c:1": decision("approve_with_condition", facts=FLAGGED),
            "review:2": decision("reject", "2027-01-03", {"flags": []}),
        }
        offline = decision("approve", "2027-01-03", mode="offline-operating-rule")
        offline["input"]["evidence"] = {"grand_total": 30000}
        decisions["po-review:d:2"] = offline
        with tempfile.TemporaryDirectory() as temp:
            today = behaviour.save(Path(temp), decisions, "2027-01-02")
            saved = json.loads((Path(temp) / "behaviour.json").read_text())
            quiet = behaviour.save(Path(temp), decisions, "2027-01-09")
        self.assertEqual(today, saved["2027-01-02"])
        self.assertEqual((quiet["decisions"], list(saved)), (0, ["2027-01-02", "2027-01-03"]))
        purchase = today["case_types"]["purchase"]
        self.assertEqual((purchase["decisions"], purchase["approval_rate"]), (2, 0.5))
        self.assertEqual(today["case_types"]["reorder_change"]["approval_rate"], 1.0)
        self.assertEqual((today["policies_proposed"], today["policies_rejected"], today["approvals_held"]), (2, 1, 1))
        self.assertEqual((today["flagged_decisions"], today["flags_overridden"]), (2, 1))
        self.assertEqual(today["overridden_flags"], {"stock_at_or_above_requested_level": 1})
        self.assertEqual(saved["2027-01-03"]["overridden_flags"], {"over_role_approval_limit": 1})


if __name__ == "__main__":
    unittest.main()
