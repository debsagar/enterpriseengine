"""Offline integration tests with a local Git remote and fake GitHub and Codex."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier, Lock
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import provenance
import run as swarm
from trace import timeline


def git(path, *args):
    return subprocess.run(
        ["git", *args], cwd=path, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True,
    ).stdout.strip()


class Fake:
    def __init__(self, root):
        self.root = root
        self.execute = swarm.execute
        self.remote = root / "origin.git"
        git(root, "init", "--bare", str(self.remote))
        self.clone = self.make_clone("clone", seed=True)
        self.issues = {}
        self.pulls = {}
        self.comments = {}
        self.reviews = {}
        self.checks = {}
        self.github_lock = Lock()
        self.calls = []
        self.worker_runs = []
        self.reviewer_runs = []
        self.default_verdict = "APPROVE"
        self.worker_hook = None
        self.claim_gate = None
        self.drop_create_reply = False

    def make_clone(self, name, *, seed=False):
        clone = self.root / name
        if seed:
            git(self.root, "init", "-b", "dev", str(clone))
        else:
            git(self.root, "clone", "-b", "dev", str(self.remote), str(clone))
        git(clone, "config", "user.email", "swarm@example.test")
        git(clone, "config", "user.name", "Swarm Test")
        if seed:
            (clone / "README.md").write_text("dev\n")
            git(clone, "add", "README.md")
            git(clone, "commit", "-m", "Seed dev")
            git(clone, "remote", "add", "origin", str(self.remote))
            git(clone, "push", "origin", "dev")
        return clone

    def runner(self, name="main", *, clone=None, dry=False):
        location = self.root / name
        location.mkdir(exist_ok=True)
        return swarm.Runner(clone or self.clone, location / "worktrees",
                            swarm.Journal(location / "log.jsonl"), command=self.command, dry=dry)

    def issue(self, number, *labels, body="", state="open"):
        self.issues[number] = {
            "number": number, "title": f"Issue {number}", "state": state,
            "body": body, "labels": [{"name": label} for label in labels],
        }

    def head(self, number):
        return git(self.remote, "rev-parse", f"refs/heads/swarm/issue-{number}")

    def external_push(self, number):
        old = self.head(number)
        new = git(self.clone, "commit-tree", f"{old}^{{tree}}", "-p", old, "-m", "External update")
        git(self.clone, "push", "origin", f"{new}:refs/heads/swarm/issue-{number}")
        return new

    def command(self, args, cwd, stdin, *, lock_fd=None):
        self.calls.append(tuple(args))
        if args[0] == "git":
            if args[1] == "push" and args[2].startswith("--force-with-lease") and self.claim_gate:
                self.claim_gate.wait(timeout=5)
            return self.execute(args, cwd, stdin, lock_fd=lock_fd)
        if args[0] == "codex":
            payload = json.loads(stdin)
            number = payload["number"]
            self.assert_prompt(args[-1], payload)
            sandbox = args[args.index("--sandbox") + 1]
            if sandbox == "danger-full-access":
                assert f"Modify files only inside {cwd}" in args[-1]
                assert "ERP-changing" in args[-1]
            sha = git(cwd, "rev-parse", "HEAD")
            if args[args.index("-m") + 1] == "gpt-6.2-astra":
                if self.worker_runs[-1][1] == "danger-full-access":
                    assert sandbox == "danger-full-access"
                self.reviewer_runs.append(sha)
                verdict = self.default_verdict
                return self.model_reply({"verdict": verdict, "findings": "Specific findings."})
            self.worker_runs.append((number, sandbox, payload["repair_findings"]))
            if self.worker_hook:
                self.worker_hook(number, cwd)
            if sandbox in {"workspace-write", "danger-full-access"}:
                (cwd / "result.txt").write_text(f"implementation {len(self.worker_runs)}\n")
            return self.model_reply({"passed": True, "command": "python -m unittest",
                                     "output": "Ran 1 test\nOK\nexit 0\npassword=hidden"})
        assert args[:2] == ["gh", "api"], args
        with self.github_lock:
            response = self.api(args[3], args[4], json.loads(stdin) if stdin else None)
            return response if isinstance(response, tuple) else (0, json.dumps(response))

    @staticmethod
    def model_reply(report):
        return 0, json.dumps({"type": "item.completed", "item": {
            "type": "agent_message", "text": json.dumps(report)}})

    @staticmethod
    def assert_prompt(prompt, payload):
        assert "MAINTAINING.md" in prompt and "acceptance" in prompt
        assert payload["body"] not in prompt or not payload["body"]

    def api(self, method, target, data):
        url = urlsplit(target)
        path = url.path.removeprefix(f"repos/{swarm.REPO}/")
        query = parse_qs(url.query)
        if path == "user":
            return {"login": "bot"}
        if path == "branches/dev":
            return {"commit": {"sha": git(self.remote, "rev-parse", "dev")},
                    "protection": {"required_status_checks": {"contexts": ["Owner gate"]}}}
        if path == "rules/branches/dev":
            return [{"type": "required_status_checks", "parameters": {"required_status_checks": [
                {"context": "Policy gate", "integration_id": 1},
            ]}}]
        if path == "actions/workflows/package.yml/runs":
            assert query["event"] in (["pull_request"], ["push"])
            return {"workflow_runs": [{"check_suite_id": 2}]}
        if path == "issues":
            return [item for item in self.issues.values() if item["state"] == "open"]
        if path == "pulls":
            if method == "GET":
                if "head" in query:
                    ref = query["head"][0].split(":", 1)[1]
                    return [self.pull(pr) for pr in self.pulls.values() if pr["head"]["ref"] == ref]
                return [self.pull(pr) for pr in self.pulls.values() if pr["state"] == "open"]
            assert method == "POST" and data["base"] == "dev"
            assert all(pr["head"]["ref"] != data["head"] for pr in self.pulls.values())
            pr = {
                **data, "number": 100 + len(self.pulls), "body": data["body"],
                "base": {"sha": git(self.remote, "rev-parse", "dev")},
                "head": {"ref": data["head"], "repo": {"full_name": swarm.REPO}},
                "user": {"login": "bot"}, "created_at": "2026-11-24T10:00:00Z",
                "merged_at": None, "state": "open", "labels": [],
            }
            self.pulls[pr["number"]] = pr
            if self.drop_create_reply:
                self.drop_create_reply = False
                return 1, ""
            return self.pull(pr)
        match = re.fullmatch(r"(issues|pulls)/(\d+)(?:/(.*))?", path)
        if match:
            kind, number, subpath = match.groups()
            number = int(number)
            if subpath in {"reviews", "comments"}:
                records = (self.reviews if subpath == "reviews" else self.comments).setdefault(number, [])
                if method == "POST":
                    assert subpath != "reviews" or data["event"] == "COMMENT"
                    record = {**data, "user": {"login": "bot"}, "author_association": "OWNER"}
                    records.append(record)
                    return record
                return records
            if subpath == "timeline":
                return []
            obj = self.issues.get(number) or self.pulls[number]
            if subpath == "labels" and method == "POST":
                obj["labels"] += [{"name": label} for label in data["labels"]
                                  if label not in swarm.labels(obj)]
                return obj["labels"]
            if (subpath or "").startswith("labels/") and method == "DELETE":
                obj["labels"] = [label for label in obj["labels"] if label["name"] != subpath.split("/")[1]]
                return obj["labels"]
            assert not subpath, (method, target)
            if method == "PATCH":
                if "labels" in data:
                    data = {**data, "labels": [{"name": label} for label in data["labels"]]}
                obj.update(data)
            return self.pull(obj) if kind == "pulls" else obj
        match = re.fullmatch(r"commits/([a-f0-9]+)/(check-runs|status)", path)
        if match:
            head, kind = match.groups()
            if kind == "status":
                return {"statuses": []}
            return {"check_runs": [
                {"id": index, "check_suite": {"id": 2},
                 "name": name, "app": {"id": 1}, "conclusion": status,
                 "status": "in_progress" if status is None else "completed"}
                for index, (name, status) in enumerate(self.checks.get(head, {}).items())
            ] + [
                {"id": 30, "check_suite": {"id": 1}, "name": "Offline workspace tests",
                 "app": {"id": 1}, "conclusion": "skipped", "status": "completed"},
            ]}
        raise AssertionError((method, target))

    def pull(self, pull):
        result = deepcopy(pull)
        match = re.fullmatch(r"swarm/issue-(\d+)", result["head"]["ref"])
        if match and "sha" not in result["head"]:
            result["head"]["sha"] = self.head(match[1])
        return result

    def ci(self, head, status="success"):
        self.checks[head] = dict.fromkeys(swarm.PACKAGE_CHECKS | {provenance.CHECK, "Owner gate", "Policy gate"}, status)


class SwarmTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.fake = Fake(Path(directory.name))
        self.stdout = io.StringIO()
        redirect = redirect_stdout(self.stdout)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def test_guard_ignores_clone_fast_forward_and_detects_protected_path(self):
        fake = self.fake
        fake.issue(1, swarm.READY)
        fake.issue(2, swarm.READY)
        root = fake.root / "protected"
        root.mkdir()
        (root / "dataset").write_text("original")

        other = fake.make_clone("unrelated")

        def mutate(number, cwd):
            if number == 1:
                git(other, "commit", "--allow-empty", "-m", "Unrelated checkout update")
                git(other, "push", "origin", "dev")
                git(fake.clone, "pull", "--ff-only", "origin", "dev")
            else:
                (root / "dataset").write_text("changed")

        fake.worker_hook = mutate
        runner = fake.runner()
        runner.sandbox = "danger-full-access"
        runner.protected_paths = (root,)
        runner.poll(1)

        self.assertEqual(swarm.labels(fake.issues[1]), {swarm.ACTIVE})
        self.assertEqual(swarm.labels(fake.issues[2]), {swarm.BLOCKED})
        self.assertEqual(len(fake.pulls), 1)
        self.assertEqual(len(fake.reviewer_runs), 1)
        self.assertIn("Protected filesystem", runner.journal.last(2, "blocked")["reason"])

    def test_atomic_claim_race_and_restart(self):
        fake = self.fake
        first = fake.runner("first")
        second = fake.runner("second", clone=fake.make_clone("other"))
        fake.claim_gate = Barrier(2)
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda runner: runner.claim(1, "swarm/issue-1"), [first, second]))
        self.assertEqual(results.count(None), 1)
        self.assertIn(fake.head(1), results)
        fake.claim_gate = None
        fake.issue(1, swarm.READY)
        owner = first if results[0] else second
        loser = second if results[0] else first
        loser.poll(1)
        self.assertEqual(swarm.labels(fake.issues[1]), {swarm.READY})
        restarted = fake.runner(owner.journal.path.parent.name, clone=owner.clone)
        restarted.poll(1)
        self.assertEqual(swarm.labels(fake.issues[1]), {swarm.ACTIVE})
        self.assertEqual(len(fake.comments[1]), 1)
        self.assertIn("Claimed by swarm worker", fake.comments[1][0]["body"])
        self.assertEqual(len(fake.pulls), 1)
        self.assertEqual(sum(call[:3] == ("git", "worktree", "add") for call in fake.calls), 1)

    def test_skip_dependencies_restart_delayed_ci_and_unblock(self):
        fake = self.fake
        fake.issue(1, swarm.READY, "ml-review", body="## Acceptance\nRun the suite.")
        fake.issue(2, swarm.READY, swarm.BLOCKED, body="## Dependencies\n- [ ] #1\n- [x] manual: confirmed")
        fake.issue(3, swarm.READY, swarm.ACTIVE)
        fake.issue(4, swarm.READY, swarm.HUMAN)
        fake.issue(5, swarm.READY, body="## Dependencies\n#1\n\n## Acceptance\nUse the suite.")
        fake.issue(6, swarm.BLOCKED)
        fake.issue(7, swarm.READY, body="## Dependencies\nExternal: maintainer confirmation")
        fake.drop_create_reply = True

        fake.runner(dry=True).poll(3)
        self.assertIn("#1: would claim", self.stdout.getvalue())
        self.assertIn("#5: wait for", self.stdout.getvalue())
        self.assertIn("#7: wait for", self.stdout.getvalue())
        self.assertTrue(all(call[:4] == ("gh", "api", "-X", "GET")
                            for call in fake.calls if call[0] != "git"))
        self.assertFalse(fake.runner(dry=True).journal.path.exists())

        fake.runner().poll(3)
        head = fake.head(1)
        self.assertEqual(len(fake.pulls), 1)
        self.assertEqual(len(fake.worker_runs), 1)
        self.assertNotIn("password=", fake.pulls[100]["body"])
        self.assertIn("Closes #1", fake.pulls[100]["body"])
        self.assertIn("Ran 1 test", fake.pulls[100]["body"])
        self.assertEqual(len(fake.comments[1]), 1)

        fake.runner().poll(3)
        self.assertEqual(fake.reviews[100][0]["commit_id"], head)
        fake.ci(head, None)
        fake.runner().poll(3)
        self.assertNotIn(swarm.HUMAN, swarm.labels(fake.pulls[100]))
        fake.ci(head)
        fake.runner().poll(3)
        self.assertEqual(swarm.labels(fake.pulls[100]), {swarm.HUMAN, "ml-review"})
        self.assertEqual(len(fake.worker_runs), 1)
        self.assertEqual(sum(call[:3] == ("git", "worktree", "add") for call in fake.calls), 1)

        fake.pulls[100].update(state="closed", merged_at="now", merged_by={"login": "maintainer"})
        fake.issues[1]["state"] = "closed"
        restart = fake.runner()
        restart.poll(3)
        terminal = [e for e in restart.journal.events[1] if e["event"] == "terminal"]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0]["by"], "maintainer")
        self.assertEqual(fake.issues[1]["state"], "closed")
        self.assertEqual(swarm.labels(fake.issues[2]), {swarm.READY})
        self.assertEqual(swarm.labels(fake.issues[6]), {swarm.BLOCKED})
        self.assertEqual(len(fake.pulls), 2)  # Unblocked #5 was already ready when #1 closed.
        fake.runner().poll(3)
        self.assertEqual(sum(e["event"] == "terminal" for e in swarm.Journal(restart.journal.path).events[1]), 1)
        self.assertEqual(fake.worker_runs[0][0], 1)
        self.assertCountEqual([args[0] for args in fake.worker_runs[1:]], [2, 5])
        self.assertEqual(len(fake.pulls), 3)
        self.assertFalse(any("merge" in args for args in fake.calls))

    def test_external_head_is_held_without_review(self):
        fake = self.fake
        fake.issue(1, swarm.READY)
        runner = fake.runner()
        runner.poll(1)
        original = fake.head(1)
        fake.ci(original)
        runner.poll(1)
        self.assertIn(swarm.HUMAN, swarm.labels(fake.pulls[100]))
        fake.external_push(1)
        runner.poll(1)
        self.assertNotIn(swarm.HUMAN, swarm.labels(fake.pulls[100]))
        self.assertEqual(fake.reviewer_runs, [original])
        self.assertEqual(runner.journal.terminal(1)["outcome"], "blocked")

    def test_review_repair_ci_infra_pause_and_fix_cap(self):
        fake = self.fake
        fake.issue(1, swarm.READY)
        runner = fake.runner()
        fake.default_verdict = "REQUEST_CHANGES"
        runner.poll(1)
        original = fake.head(1)
        fake.default_verdict = "APPROVE"
        runner.poll(1)
        first_fix = fake.head(1)
        self.assertNotEqual(first_fix, original)
        self.assertIn("Specific findings", fake.worker_runs[-1][2])

        dev = git(fake.remote, "rev-parse", "dev")
        fake.ci(dev)
        fake.ci(first_fix)
        fake.checks[dev]["Offline workspace tests"] = "failure"
        fake.checks[first_fix]["Offline workspace tests"] = "failure"
        runner.poll(1)
        self.assertEqual(runner.journal.last(1, "ci")["status"], "infra-failure")
        self.assertEqual(runner.journal.last(1, "start")["round"], 1)
        self.assertEqual(runner.journal.status()[0]["stage"], "ci")
        self.assertEqual(fake.head(1), first_fix)

        fake.ci(dev)
        runner = fake.runner()
        runner.poll(1)
        second_fix = fake.head(1)
        self.assertNotEqual(second_fix, first_fix)
        self.assertIn("CI failed", fake.worker_runs[-1][2])
        fake.ci(second_fix, "failure")
        fake.runner().poll(1)
        self.assertEqual(swarm.labels(fake.issues[1]), {swarm.BLOCKED})
        self.assertIn("Fix limit exhausted", fake.comments[1][-1]["body"])
        self.assertEqual(len(fake.worker_runs), 3)
        self.assertEqual(len(fake.pulls), 1)
        self.assertFalse(any("merge" in args for args in fake.calls))

    def test_foreign_prs_and_closed_issues_are_untouched(self):
        fake = self.fake
        fake.issue(1, swarm.READY, state="closed")
        fake.issue(2, swarm.READY)
        fake.issue(3, swarm.READY)
        fake.pulls[100] = {"number": 100, "head": {"ref": "chat/maintenance"},
                           "body": "Fixes debsagar/enterpriseengine#2", "state": "open"}
        fake.pulls[101] = {"number": 101, "head": {"ref": "swarm/issue-3", "sha": "external"},
                           "body": "", "state": "open"}
        fake.runner().poll(1)
        self.assertFalse(fake.worker_runs)
        self.assertFalse(any(call[:4] in (("gh", "api", "-X", "POST"), ("gh", "api", "-X", "PATCH"))
                             for call in fake.calls))

    def test_provenance_trailers_redaction_and_shared_log(self):
        fake = self.fake
        fake.issue(1, swarm.READY)
        fake.issue(2, swarm.READY, body="token=do-not-retain")
        runner = fake.runner()
        fake.worker_hook = lambda number, cwd: (cwd / "PROJECT-LOG.jsonl").touch() if number == 1 else None
        runner.poll(1)
        self.assertEqual(runner.journal.terminal(1)["outcome"], "blocked")
        record = runner.record(2)
        pr = fake.pulls[100]
        commits = [{"sha": sha, "commit": {"message": git(fake.clone, "show", "-s", "--format=%B", sha)}}
                   for sha in git(fake.clone, "rev-list", f"{record['base']}..{fake.head(2)}").splitlines()]
        provenance.check(pr["body"], commits)
        with self.assertRaisesRegex(ValueError, "cannot change"):
            provenance.check(pr["body"], commits, shared_log=True)
        with self.assertRaisesRegex(ValueError, "matching Change-Actor"):
            provenance.check(pr["body"], [{"sha": fake.head(2), "commit": {"message": "missing"}}])
        transcript = (runner.records / f"{record['run']}.jsonl").read_text()
        self.assertNotIn("do-not-retain", transcript)
        self.assertNotIn("hidden", transcript)
        self.assertIn("model-output", transcript)
        self.assertIn(record["transcript"], fake.comments[100][0]["body"])
        self.assertEqual(swarm.dependencies("## Dependencies\n- [x] manual: done", unblocking=True),
                         (set(), False))
        self.assertEqual(swarm.redact({"access_token": "unmarked", "output": "OK"}), {"output": "OK"})
        self.assertEqual(swarm.dependencies("## Dependencies\n- [ ] manual: also #2"), (set(), True))

    def test_trace_joins_pr_and_journal(self):
        fake = self.fake
        fake.issue(1, swarm.READY)
        runner = fake.runner()
        runner.poll(1)
        sha = fake.head(1)

        def api(endpoint, *, pages=False):
            if endpoint.endswith("/pulls/100"):
                return {**fake.pull(fake.pulls[100]), "merged_at": "2026-11-24T11:00:00Z",
                        "merged_by": {"login": "maintainer"}, "merge_commit_sha": sha}
            if endpoint.endswith("/check-runs?per_page=100"):
                return [{"check_runs": []}]
            return [[]]

        result = timeline(api, "100", runner.journal.path.parent)
        self.assertIn("model-start", result)
        self.assertIn("PR opened by bot", result)
        self.assertIn("merged by maintainer", result)

    def test_wrong_runner_pin_leaves_claim_open(self):
        fake = self.fake
        fake.issue(1, swarm.READY)
        owner = fake.runner()
        owner.poll(1)
        new_runner = fake.runner()
        new_runner.runner_sha = "f" * 40
        new_runner.poll(1)
        self.assertIsNone(new_runner.journal.terminal(1))
        self.assertEqual(new_runner.journal.status()[0]["status"], "paused")
        self.assertEqual(new_runner.journal.status()[0]["runner"], owner.runner_sha)
        self.assertEqual(len(fake.worker_runs), 1)

    def test_human_template_and_no_issue_reason(self):
        record = {"actor": "human", "issue": "none", "issue_reason": "Routine cleanup", "base": "a" * 40}
        commits = [{"sha": "b" * 40, "commit": {"message": provenance.commit_message("Cleanup", record)}}]
        provenance.check(provenance.block(record), commits)
        with self.assertRaisesRegex(ValueError, "issue_reason"):
            provenance.check(provenance.block({**record, "issue_reason": ""}), commits)


if __name__ == "__main__":
    unittest.main()
