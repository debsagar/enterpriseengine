"""The labelled decision set stays consistent with the production prompt and scores offline (R18)."""

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from environments.norvale.native import judgment, long_run, router, score_decisions

CASES = score_decisions.load()
# A pre-approval limit is not a cap on judgment, so this one flag is allowed on a valid approval.
ALLOWED_ON_APPROVAL = {"rule-po-above-policy": ["over_policy_approval_limit"]}


class DecisionCases(unittest.TestCase):
    def test_forty_labelled_cases_with_rationales_and_valid_approvals(self):
        self.assertEqual(len({c["id"] for c in CASES}), 40)
        self.assertEqual(Counter(c["label"] for c in CASES), {"approve": 17, "request_info": 21, "reject": 2})
        for case in CASES:
            self.assertIn(case["label"], case["acceptable"])
            self.assertTrue(set(case["acceptable"]) <= set(judgment.ACTIONS))
            approving = {a in judgment.APPROVALS for a in case["acceptable"]}
            self.assertEqual(len(approving), 1, case["id"])
            self.assertTrue(case["rationale"].strip() and "\n" not in case["rationale"])
        text = json.dumps(CASES)
        for private in ("arrival_due", "demand_scale", "private_regime", "custom_norvale_operation"):
            self.assertNotIn(private, text)

    def test_questions_offer_no_action_outside_the_graded_set(self):
        for case in CASES:
            for earlier in ("defer", "investigate"):
                self.assertNotIn(earlier, case["question"].lower(), case["id"])

    def test_valid_approvals_carry_no_contradiction_flag(self):
        for case in CASES:
            if case["label"] == "approve":
                found = [f["flag"] for f in score_decisions.prompt(case)["facts"]["flags"]]
                self.assertEqual(found, ALLOWED_ON_APPROVAL.get(case["id"], []), case["id"])

    def test_stubbed_run_scores_resumes_and_applies_holds(self):
        sent = []

        def approve_everything(name, request, on_attempt):
            sent.append(request)
            answer = dict(action="approve", reason="ok", cites=["facts.flags"])
            return {
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.001},
            }

        with tempfile.TemporaryDirectory() as temp, patch("builtins.print"):
            out = str(Path(temp) / "stub.jsonl")
            env = {"NORVALE_OFFLINE": "0", "OPENAI_API_KEY": "stub", "NORVALE_SPEND_CEILING_USD": ""}
            with patch.dict("os.environ", env):
                with patch.object(router, "provider_json", approve_everything):
                    result = score_decisions.main(["--model", "stub", "--out", out])
                    again = score_decisions.main(["--model", "stub", "--out", out])
        self.assertEqual((len(sent), result), (40, again))
        self.assertEqual((len(result["proposed"]["wrong_approvals"]), result["proposed"]["wrong_refusals"]), (23, []))
        # Code holds only the over-authority approval; the other bad approvals are the model's to catch.
        self.assertEqual(len(result["executed"]["wrong_approvals"]), 22)
        self.assertNotIn("trap-over-authority", result["executed"]["wrong_approvals"])
        self.assertEqual((result["cost"], result["cost_per_call"]), (0.04, 0.001))

    def test_spend_ceiling_stops_the_run_and_keeps_paid_answers(self):
        def priced(name, request, on_attempt):
            return {
                "choices": [{"finish_reason": "stop", "message": {"content": '{"action": "reject"}'}}],
                "usage": {"cost": 0.4},
            }

        env = {"NORVALE_OFFLINE": "0", "OPENAI_API_KEY": "stub", "NORVALE_SPEND_CEILING_USD": "1"}
        with tempfile.TemporaryDirectory() as temp, patch("builtins.print"), patch.dict("os.environ", env):
            out = Path(temp) / "capped.jsonl"
            with patch.object(router, "provider_json", priced):
                with self.assertRaisesRegex(long_run.SpendCeilingExceeded, "ceiling"):
                    score_decisions.main(["--model", "stub", "--out", str(out)])
            self.assertEqual(len(out.read_text().splitlines()), 3)

    def test_resume_refuses_changed_cases_and_keeps_replies_without_usage(self):
        def cut_off(name, request, on_attempt):
            return {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}

        env = {"NORVALE_OFFLINE": "0", "OPENAI_API_KEY": "stub", "NORVALE_SPEND_CEILING_USD": ""}
        with tempfile.TemporaryDirectory() as temp, patch("builtins.print"), patch.dict("os.environ", env):
            out, changed = Path(temp) / "cut.jsonl", Path(temp) / "cases.json"
            with patch.object(router, "provider_json", cut_off):
                result = score_decisions.main(["--model", "stub", "--out", str(out)])
            self.assertEqual((result["answered"], result["invalid"], "cost" in result), (40, 40, False))
            edited = json.loads(score_decisions.CASES.read_text())
            edited["cases"][0]["question"] += " Now."
            changed.write_text(json.dumps(edited))
            with self.assertRaises(SystemExit), patch("sys.stderr"):
                score_decisions.main(["--model", "stub", "--out", str(out), "--cases", str(changed)])
        usage = {"id": CASES[0]["id"], "usage": {"cost": None}}
        self.assertNotIn("cost", score_decisions.score(CASES, [usage]))

    def test_run_without_credentials_stops_without_scoring(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"NORVALE_OFFLINE": "1"}):
            out = Path(temp) / "none.jsonl"
            with self.assertRaisesRegex(RuntimeError, "no approval fallback"):
                score_decisions.main(["--model", "stub", "--out", str(out)])
            self.assertEqual(out.read_text(), "")


if __name__ == "__main__":
    unittest.main()
