"""Offline integration tests with a local Git remote and fake GitHub and Codex."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from urllib.parse import parse_qs, urlsplit


spec = importlib.util.spec_from_file_location("swarm", Path(__file__).resolve().parents[1] / "run.py")
swarm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(swarm)


def git(path, *args):
    return subprocess.run(
        ["git", *args], cwd=path, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True,
    ).stdout.strip()


class Fake:
    def __init__(self, root):
        self.root = root
        self.remote = root / "origin.git"
        git(root, "init", "--bare", str(self.remote))
        self.clone = self.make_clone("clone", seed=True)
        self.issues = {}
        self.pulls = {}
        self.comments = {}
        self.reviews = {}
        self.checks = {}
        self.calls = []
        self.worker_runs = []
        self.reviewer_runs = []
        self.verdicts = {}
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
            return swarm.execute(args, cwd, stdin, lock_fd=lock_fd)
        if args[0] == "codex":
            payload = json.loads(stdin)
            number = payload["number"]
            self.assert_prompt(args[-1], payload)
            sha = git(cwd, "rev-parse", "HEAD")
            if args[args.index("-m") + 1] == "gpt-6.2-astra":
                self.reviewer_runs.append(sha)
                verdict = self.verdicts.get(sha, "APPROVE")
                return 0, json.dumps({"verdict": verdict, "findings": "Specific findings."})
            sandbox = args[args.index("--sandbox") + 1]
            self.worker_runs.append((number, sandbox, payload["repair_findings"]))
            if sandbox == "workspace-write":
                (cwd / "result.txt").write_text(f"implementation {len(self.worker_runs)}\n")
            return 0, json.dumps({"passed": True, "command": "python -m unittest",
                                  "output": "Ran 1 test\nOK\nexit 0\npassword=hidden"})
        assert args[:2] == ["gh", "api"], args
        response = self.api(args[3], args[4], json.loads(stdin) if stdin else None)
        return response if isinstance(response, tuple) else (0, json.dumps(response))

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
            return {"protection": {"required_status_checks": {"contexts": ["Owner gate"]}}}
        if path == "rules/branches/dev":
            return [{"type": "required_status_checks", "parameters": {"required_status_checks": [
                {"context": "Policy gate", "integration_id": 1},
            ]}}]
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
                "head": {"ref": data["head"]}, "merged_at": None, "state": "open", "labels": [],
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
            obj = self.issues.get(number) or self.pulls[number]
            if subpath == "labels" and method == "POST":
                obj["labels"] += [{"name": label} for label in data["labels"]
                                  if label not in swarm.labels(obj)]
                return obj["labels"]
            if subpath == "labels/blocked" and method == "DELETE":
                obj["labels"] = [label for label in obj["labels"] if label["name"] != swarm.BLOCKED]
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
                {"name": name, "app": {"id": 1}, "conclusion": status,
                 "status": "in_progress" if status is None else "completed"}
                for name, status in self.checks.get(head, {}).items()
            ]}
        raise AssertionError((method, target))

    def pull(self, pull):
        result = deepcopy(pull)
        match = re.fullmatch(r"swarm/issue-(\d+)", result["head"]["ref"])
        if match:
            result["head"]["sha"] = self.head(match[1])
        return result

    def ci(self, head, status="success"):
        self.checks[head] = dict.fromkeys(swarm.PACKAGE_CHECKS | {"Owner gate", "Policy gate"}, status)


class SwarmTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.fake = Fake(Path(directory.name))
        self.stdout = io.StringIO()
        redirect = redirect_stdout(self.stdout)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

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
        fake.issue(2, swarm.READY, swarm.BLOCKED, body="## Dependencies\n#1")
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
        self.assertTrue(all(call[:4] == ("gh", "api", "-X", "GET") for call in fake.calls))
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

        fake.pulls[100].update(state="closed", merged_at="now")
        fake.runner().poll(3)
        self.assertEqual(fake.issues[1]["state"], "closed")
        self.assertEqual(swarm.labels(fake.issues[2]), {swarm.READY})
        self.assertEqual(swarm.labels(fake.issues[6]), {swarm.BLOCKED})
        self.assertEqual(len(fake.pulls), 1)
        fake.runner().poll(3)
        self.assertEqual(fake.worker_runs[0][0], 1)
        self.assertCountEqual([args[0] for args in fake.worker_runs[1:]], [2, 5])
        self.assertEqual(len(fake.pulls), 3)
        self.assertFalse(any("merge" in args for args in fake.calls))

    def test_stale_approval_failing_ci_and_two_fix_cap(self):
        fake = self.fake
        fake.issue(1, swarm.READY, body="## Acceptance\nRun the suite.")
        runner = fake.runner()
        runner.poll(1)
        original = fake.head(1)
        fake.ci(original)
        runner.poll(1)
        self.assertIn(swarm.HUMAN, swarm.labels(fake.pulls[100]))

        current = fake.external_push(1)
        fake.verdicts[current] = "REQUEST_CHANGES"
        runner.poll(1)
        self.assertNotIn(swarm.HUMAN, swarm.labels(fake.pulls[100]))
        self.assertEqual(fake.reviewer_runs, [original, current])
        self.assertIn(current, fake.pulls[100]["body"])
        runner.poll(1)
        first_fix = fake.head(1)
        self.assertNotEqual(first_fix, current)
        self.assertIn("Specific findings", fake.worker_runs[-1][2])

        fake.ci(first_fix, "failure")
        fake.runner().poll(1)
        second_fix = fake.head(1)
        self.assertNotEqual(second_fix, first_fix)
        self.assertIn("CI failed", fake.worker_runs[-1][2])
        fake.ci(second_fix, "failure")
        fake.runner().poll(1)
        self.assertEqual(swarm.labels(fake.issues[1]), {swarm.BLOCKED})
        self.assertIn("Fix limit exhausted", fake.comments[1][-1]["body"])
        self.assertEqual([mode for _, mode, _ in fake.worker_runs].count("workspace-write"), 3)
        self.assertEqual(len(fake.pulls), 1)
        self.assertEqual(sum(call[:3] == ("git", "worktree", "add") for call in fake.calls), 1)
        self.assertNotIn(swarm.HUMAN, swarm.labels(fake.pulls[100]))
        self.assertFalse(any("merge" in args for args in fake.calls))


if __name__ == "__main__":
    unittest.main()
