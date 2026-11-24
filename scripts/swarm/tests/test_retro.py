"""Offline acceptance for R19/R20, using paginated GitHub responses and role journals."""

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
import sys
import shlex
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/swarm"))
import provenance
import retro
from roles import Issues, accepted_commands
from run import Journal, role_record


class RetrospectiveTest(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "SPEC.md").write_bytes((ROOT / "SPEC.md").read_bytes())
        (self.root / "DECISIONS.md").write_text("Settled decisions\n")
        (self.root / "ROADMAP.md").write_text("# Roadmap\n\n## Path to v1.0\n\nKeep this goal.\n")
        self.milestones = [dict(number=3, title="v0.3.0", state="open", open_issues=1, closed_issues=1)]
        self.issues = [dict(number=1, title="Steam", body="R12", state="closed", milestone=3),
                       dict(number=2, title="Fault", body="<!-- swarm-regression:abc -->", state="open", milestone=3)]
        sha = "a" * 40
        source = role_record("author", sha, sha, "#1", "run1", model="gpt-6.2-sol")
        self.prs = [dict(number=9, title="Ship steam component", body=provenance.block(source),
                         head={"ref": "swarm/issue-1"}, created_at="2026-11-01T00:00:00Z",
                         merged_at="2026-11-01T12:00:00Z")]
        for role in ["issues", "swarm", "merger"]:
            (self.root / role).mkdir()
        self.client = Issues(self.root, self.root, Journal(self.root / "issues/log.jsonl"),
                             command=self.gh, runner_sha=sha)
        merger = Journal(self.root / "merger/history.jsonl")
        for status in ["failure", "failure", "infra-failure", "success"]:
            merger.add(9, "decision", head=sha, status=status, reason="Offline CI")
        merger.add(9, "bounce", reason="Package test failed")
        review = Journal(self.root / "swarm/log.jsonl")
        review.add(9, "claude-unavailable", ts="2026-11-01T00:00:00Z")
        review.add(9, "reviewer-verdict", ts="2026-11-01T02:00:00Z", provenance={"reviewer_fallback": True})
        review.add(9, "swarm-verdict", acceptance={"commands": [
            dict(command="python norvale_dry_run.py --compare fixture", exit_code=1, output="orders +1"),
            dict(command="compare_autotwin", exit_code=0, output="trace count delta -2")]})

    def gh(self, args, cwd, stdin, **kwargs):
        self.assertEqual(args[:3], ["gh", "api", "-X"])
        method, url = args[3], urlsplit(args[4])
        path = url.path.removeprefix(self.client.repo + "/")
        data = json.loads(stdin) if stdin else {}
        if method == "GET":
            if path == "branches/dev":
                result = {"commit": {"sha": self.client.runner_sha}}
            elif path.startswith("labels/"):
                result = {"name": path[7:]}
            elif path == "milestones":
                result = self.milestones
            elif path == "pulls":
                result = self.prs
            else:
                self.assertEqual(path, "issues")
                number = parse_qs(url.query).get("milestone")
                result = [i for i in self.issues if not number or i.get("milestone") == int(number[0])]
        else:
            self.assertFalse(self.client.dry)
            self.assertEqual(method, "POST")
            if path == "issues":
                result = dict(number=100 + len(self.issues), state="open", **data)
                result["labels"] = [{"name": name} for name in data["labels"]]
                self.issues.append(result)
            else:
                self.assertEqual(path, "milestones")
                result = dict(number=max(m["number"] for m in self.milestones) + 1, state="open", closed_issues=0, open_issues=1, **data)
                self.milestones.append(result)
        return 0, json.dumps(result)

    def test_milestone_plan_and_repeat(self):
        retro.poll(self.client, self.root)
        docs = self.issues[-1]["body"]
        for key in retro.METRICS:
            self.assertIn(f"| {key} |", docs)
        for evidence in ["12.00", "33.33", "2.00", "orders +1", "trace count delta -2",
                         "gpt-6.2-sol | 100.00", "PR #9 — Package test failed", "Ship steam component (R12)",
                         "docs/retros/v0.3.0.md", "+++ b/ROADMAP.md", "Depends on #2"]:
            self.assertIn(evidence, docs)
        process = [i for i in self.issues if {"name": "process"} in i.get("labels", [])]
        self.assertEqual(len(process), 3)
        for issue in process:
            self.assertEqual(issue["milestone"], 4)
            self.assertIn("Expected decrease", issue["body"])
            self.assertIn({"name": "size:small"}, issue["labels"])
        count = len(self.issues)
        retro.poll(self.client, self.root)
        self.assertEqual(len(self.issues), count)
        # Immediate closure after the weekly pass, and an explicit replay.
        self.milestones[0].update(closed_issues=2, open_issues=0)
        self.issues[1]["state"] = "closed"
        retro.poll(self.client, self.root, "v0.3.0")
        self.assertTrue(self.client.journal.last(-3, "retro")["complete"])
        count = len(self.issues)
        retro.poll(self.client, self.root, "v0.3.0")
        self.assertEqual(len(self.issues), count)

    def test_unassigned_dev_finding_attributed_by_merge_sha(self):
        sha = "b" * 40
        self.prs[0]["merge_commit_sha"] = sha
        for n, head in [(35, sha), (36, "c" * 40)]:
            self.issues.append(dict(number=n, state="open", title="Dev check", body=(
                f"<!-- swarm-regression:{n} -->\n\nDev `{head}`\n\nR17 order count changed.")))
        swarm = Journal(self.root / "swarm/log.jsonl")
        swarm.add(0, "verdict", head=sha, acceptance={"commands": [
            dict(command="python norvale_dry_run.py --compare fixture", exit_code=1, output="lot count +1")]})
        retro.poll(self.client, self.root)
        report = self.issues[-1]["body"]
        self.assertIn("| Swarm regressions | 2 |", report)
        self.assertIn("Swarm finding #35 — R17 order count changed.", report)
        self.assertNotIn("Swarm finding #36", report)
        self.assertIn("lot count +1", report)

    def test_publication_handoff_and_document_acceptance(self):
        retro.poll(self.client, self.root)
        body = self.issues[-1]["body"]
        report = body.split("~~~~markdown\n")[1].split("~~~~\n")[0]
        diff = body.split("~~~~diff\n")[1].split("~~~~\n")[0]
        location = self.root / "docs/retros/v0.3.0.md"
        location.parent.mkdir(parents=True)
        location.write_text(report)
        subprocess.run(["patch", "-s", "-p1"], cwd=self.root, input=diff, text=True, check=True)
        command, = accepted_commands(body)
        subprocess.run(shlex.split(command), cwd=self.root, check=True)
        self.assertIn("Keep this goal.", (self.root / "ROADMAP.md").read_text())
        self.assertEqual((self.root / "DECISIONS.md").read_text(), "Settled decisions\n")
        self.assertEqual((self.root / "SPEC.md").read_bytes(), (ROOT / "SPEC.md").read_bytes())
        count = len(self.issues)
        retro.poll(self.client, self.root, "v0.3.0")
        self.assertEqual(count, len(self.issues))

    def test_next_milestone_measures_and_queues_reversal(self):
        retro.poll(self.client, self.root)
        prior = self.client.journal.last(-3, "retro")["metrics"]
        self.assertEqual(prior[retro.MISSING], 1)
        experiment = next(i for i in self.issues if "retro-experiment:2" in i.get("body", ""))
        experiment["state"] = "closed"
        for n in [30, 31]:
            self.issues.append(dict(number=n, title="Fault", state="open", body="", milestone=4))
        self.issues.append(dict(number=32, title="Amend SPEC.md", state="open", body="R19", milestone=4))
        retro.poll(self.client, self.root, "v0.4.0")
        self.assertIn("| Issues without requirement ID | 2 | 1 |", self.issues[-1]["body"])
        revert = next(i for i in self.issues if i["title"] == f"Revert process change #{experiment['number']}")
        self.assertIn({"name": "agent-ready"}, revert["labels"])
        decision = next(i for i in self.issues if i["title"] == "Decide the contract proposal in #32")
        self.assertIn({"name": "needs-human"}, decision["labels"])
        self.assertNotIn({"name": "agent-ready"}, decision["labels"])

    def test_weekly_refresh_and_automatic_completion(self):
        retro.poll(self.client, self.root)
        last = self.client.journal.last(-3, "retro")
        last["ts"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        retro.poll(self.client, self.root)
        self.assertIsNot(last, self.client.journal.last(-3, "retro"))
        self.milestones[0].update(open_issues=0, closed_issues=2)
        self.issues[1]["state"] = "closed"
        retro.poll(self.client, self.root)
        self.assertTrue(self.client.journal.last(-3, "retro")["complete"])

    def test_cli_read_only_and_previous_experiment(self):
        self.client.dry = True
        output = StringIO()
        with redirect_stdout(output), patch("run.CLONE", self.root), patch("run.execute", return_value=(0, "a"*40)), \
                patch.object(Issues, "api", self.client.api), patch.dict("os.environ", {"SWARM_RETRO_OFFLINE": ""}), \
                patch.object(sys, "argv", ["run.py", "issues", "--once", "--dry-run", "--retro", "v0.3.0",
                                           "--state-dir", str(self.root)]):
            import run
            self.assertEqual(run.main(), 0)
        self.assertIn("+++ b/ROADMAP.md", output.getvalue())
        self.assertEqual(len(self.issues), 2)
        self.assertFalse(self.client.journal.path.exists())
        issue = dict(number=20, state="closed", title="CI preflight", body="<!-- retro-experiment:0 -->")
        tasks, outcomes = retro.plan_tasks([issue], {retro.INFRA: 10}, {retro.INFRA: 5}, set())
        self.assertIn("Revert process change #20", [t["title"] for t in tasks])
        self.assertIn("5 → 10", outcomes[0])


if __name__ == "__main__":
    unittest.main()
