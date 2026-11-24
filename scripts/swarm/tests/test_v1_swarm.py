"""Offline integration tests with a local Git remote and fake GitHub and Codex."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Lock
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import provenance
import run as swarm
from trace import timeline
from roles import Issues, Merger, Swarm, eligible_reviewers, verdict_record


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
        self.statuses = {}
        self.repo_labels = set()
        self.claude_errors = {}
        self.review_calls = []
        self.author_calls = []
        self.github_lock = Lock()
        self.calls = []
        self.worker_runs = []
        self.validation_runs = []
        self.validation_hook = None
        self.setup_status = 0
        self.issue_plans = {}
        self.merge_runs = []
        self.merger_login = "bot"
        self.strict = True
        self.findings = {"blocking_findings": [], "advisory_findings": []}
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
            (clone / "SPEC.md").write_text("Owner specification")
            (clone / "DECISIONS.md").write_text("Owner decisions")
            git(clone, "add", "README.md", "SPEC.md", "DECISIONS.md")
            git(clone, "commit", "-m", "Seed dev")
            git(clone, "remote", "add", "origin", str(self.remote))
            git(clone, "push", "origin", "dev")
        return clone

    def runner(self, name="main", *, clone=None, dry=False, role=swarm.Runner):
        location = self.root / name / role.role
        location.mkdir(parents=True, exist_ok=True)
        log = "history.jsonl" if role is Merger else "log.jsonl"
        def command(args, cwd, stdin, *, lock_fd=None):
            if role is Merger and args[:5] == ["gh", "api", "-X", "GET", "user"]:
                return 0, json.dumps({"login": self.merger_login})
            return self.command(args, cwd, stdin, lock_fd=lock_fd)

        runner = role(clone or self.clone, location / "worktrees", swarm.Journal(location / log),
                      command=command, dry=dry)
        runner.protected_paths = (self.root / "protected-data",)
        return runner

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
        if tuple(args) == swarm.SETUP:
            return self.setup_status, "Failed to download arro3-core"
        if args[:3] == ["gh", "api", "graphql"]:
            closer = self.issues[int(args[-1].split("=")[1])].get("closer")
            return 0, json.dumps({"data":{"repository":{"issue":{"timelineItems":{"nodes":[{"closer":closer}]}}}}})
        if args[0] == "git":
            if args[1] == "push" and args[2].startswith("--force-with-lease") and self.claim_gate:
                self.claim_gate.wait(timeout=5)
            if args[1] == "fetch":
                for arg in args:
                    match = re.match(r"\+refs/pull/(\d+)/head:", arg)
                    if match:
                        number = int(match[1])
                        git(self.remote, "update-ref", f"refs/pull/{number}/head",
                            self.pull(self.pulls[number])["head"]["sha"])
            return self.execute(args, cwd, stdin, lock_fd=lock_fd)
        if args[:3] == ["gh", "pr", "merge"]:
            number = int(args[3])
            head = self.pull(self.pulls[number])["head"]["sha"]
            assert args[4:] == ["--repo", swarm.REPO, "--rebase", "--match-head-commit", head]
            self.merge_runs.append(number)
            git(self.remote, "update-ref", "refs/heads/dev", head)
            self.pulls[number].update(state="closed", merged_at="now", merge_commit_sha=head,
                                      merged_by={"login": self.merger_login})
            return 0, ""
        if args[0] == "claude":
            _, payload = stdin.split("\n\n", 1)
            payload = json.loads(payload)
            number = payload["number"]
            assert args[:6] == ["claude", "-p", "--model", "claude-opus-5-5", "--effort", "low"]
            assert args[args.index("--tools") + 1] == "Read,Grep,Glob"
            assert payload["SPEC.md"] == "Owner specification" and payload["DECISIONS.md"] == "Owner decisions"
            assert "Acceptance test command" in payload["body"] and "diff --git" in payload["diff"]
            assert payload["acceptance_output"]["summary"] == "Checks passed."
            self.review_calls.append((number, "claude-opus-5-5", "low"))
            if number in self.claude_errors:
                return 1, self.claude_errors[number]
            return 0, json.dumps({"structured_output": {"passed": True, "findings": "Reviewed."}})
        if args[0] == "codex":
            payload = json.loads(stdin)
            number = payload["number"]
            self.assert_prompt(args[-1], payload)
            sandbox = args[args.index("--sandbox") + 1]
            if sandbox == "danger-full-access":
                assert f"Modify files only inside {cwd}" in args[-1]
                assert "ERP-changing" in args[-1]
            sha = git(cwd, "rev-parse", "HEAD")
            model = args[args.index("-m") + 1]
            effort = next(value.split('"')[1] for value in args if value.startswith("model_reasoning_effort="))
            if "acceptance_output" in payload:
                self.review_calls.append((number, model, effort))
                return self.model_reply({"passed": True, "findings": "Fallback reviewed."})
            if "command_results" in payload and "candidate" not in payload:
                commands = [entry["command"] for entry in payload["command_results"]]
                self.validation_runs.append((number, sha, commands))
                if number and self.validation_hook:
                    self.validation_hook(number, cwd)
                assert " ".join(swarm.SETUP) not in commands
                return self.model_reply({"passed": True, "command": "", "output": "Checks passed."})
            if "merge_report" in payload:
                assert model == "gpt-6-luna" and sandbox == swarm.DEFAULT_SANDBOX
                return self.model_reply({"summary": "Implemented the scoped change."})
            if "candidate" in payload:
                assert model == "gpt-6-luna" and sandbox == swarm.DEFAULT_SANDBOX
                self.validation_runs.append((number, sha, "second pass"))
                return self.model_reply(self.findings)
            if "issues" in payload:
                assert model == "gpt-5.6-terra" and sandbox == swarm.DEFAULT_SANDBOX
                return self.model_reply({"size": "large", "scope": "", **self.issue_plans.get(number, {
                    "classification": "distinct", "duplicate": "", "impact": "Affects contracts.",
                    "prerequisites": "",
                })})
            self.author_calls.append((number, model, effort))
            self.worker_runs.append((number, sandbox, payload["repair_findings"]))
            if self.worker_hook:
                self.worker_hook(number, cwd)
            if sandbox in {"workspace-write", "danger-full-access"}:
                name = f"result-{number}.txt"
                scope = payload["claim_scope"]
                target = cwd / (scope[0] if scope and name not in scope else name)
                if not target.suffix:
                    target /= f"result-{number}.txt"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"implementation {len(self.worker_runs)}\n")
            code, reply = self.model_reply({"passed": True, "command": "python -m unittest",
                                     "output": "Ran 1 test\nOK\nexit 0\npassword=hidden"})
            event = {"type": "command_execution", "command": "uv run python -m unittest" if "uv run" in payload["body"] else "python -m unittest",
                     "aggregated_output": "Ran 1 test\nOK\nexit 0", "exit_code": 0}
            return code, json.dumps({"type": "item.completed", "item": event}) + "\n" + reply
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
        assert "MAINTAINING.md" in prompt and "acceptance" in prompt.lower()
        assert payload["body"] not in prompt or not payload["body"]

    def api(self, method, target, data):
        url = urlsplit(target)
        path = url.path.removeprefix(f"repos/{swarm.REPO}/")
        query = parse_qs(url.query)
        if path == "user":
            return {"login": "bot"}
        if path == "labels" and method == "POST":
            self.repo_labels.add(data["name"])
            return data
        if path.startswith("labels/"):
            name = path.split("/", 1)[1]
            return {"name": name} if name in self.repo_labels else (1, "")
        if path.startswith("statuses/"):
            assert method == "POST"
            statuses = self.statuses.setdefault(path.split("/", 1)[1], {})
            statuses[data["context"]] = {**data, "creator": {"login": "bot"}}
            return data
        if path == "branches/dev":
            return {"commit": {"sha": git(self.remote, "rev-parse", "dev")},
                    "protection": {"required_status_checks": {"contexts": ["Owner gate"]}}}
        if path == "rules/branches/dev":
            return [{"type": "required_status_checks", "parameters": {"required_status_checks": [
                {"context": "Policy gate", "integration_id": 1},
                *({"context": context} for context in swarm.VERDICT_CONTEXTS.values()),
            ], "strict_required_status_checks_policy": self.strict}}]
        if path == "actions/workflows/package.yml/runs":
            assert query["event"] in (["pull_request"], ["push"])
            return {"workflow_runs": [{"check_suite_id": 2}]}
        if path == "issues":
            if method == "POST":
                number = max(self.issues, default=0) + 1
                self.issue(number, *data.get("labels", []), body=data["body"])
                self.issues[number]["title"] = data["title"]
                return self.issues[number]
            return [item for item in self.issues.values() if item["state"] == "open"]
        if path == "pulls":
            if method == "GET":
                if query.get("state") == ["closed"]:
                    return [pr for pr in self.pulls.values() if pr["state"] == "closed"
                            and pr.get("base", {}).get("ref") == query["base"][0]]
                if "head" in query:
                    ref = query["head"][0].split(":", 1)[1]
                    return [self.pull(pr) for pr in self.pulls.values() if pr["head"]["ref"] == ref]
                return [self.pull(pr) for _, pr in sorted(self.pulls.items()) if pr["state"] == "open"]
            assert method == "POST" and data["base"] == "dev"
            assert all(pr["head"]["ref"] != data["head"] for pr in self.pulls.values())
            pr = {
                **data, "number": 99 + int(data["head"].split("-")[-1]), "body": data["body"],
                "base": {"sha": git(self.remote, "rev-parse", "dev"), "ref": "dev"},
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
        match = re.fullmatch(r"commits/([a-f0-9]+)/(check-runs|status|statuses)", path)
        if match:
            head, kind = match.groups()
            if kind == "statuses":
                return list(self.statuses.get(head, {}).values())
            if kind == "status":
                return {"statuses": [{key: value for key, value in entry.items() if key != "creator"}
                                     for entry in self.statuses.get(head, {}).values()]}
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
    real_command_result = Swarm.command_result

    def setUp(self):
        fake_commands = patch.object(Swarm, "command_result", side_effect=lambda command, path:
                                     {"command": command, "exit_code": 0, "output": "ok"})
        self.commands = fake_commands.start()
        self.addCleanup(fake_commands.stop)
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
        runner.poll()

        self.assertEqual(swarm.labels(fake.issues[1]), {swarm.ACTIVE})
        fake.pulls[100].update(state="closed", merged_at="now")
        runner.poll()
        self.assertEqual(swarm.labels(fake.issues[2]), {swarm.BLOCKED})
        self.assertEqual(len(fake.pulls), 1)
        self.assertEqual(fake.validation_runs, [])
        self.assertIn("Protected filesystem", runner.journal.last(2, "blocked")["reason"])

    def test_atomic_claim_race_and_restart(self):
        fake = self.fake
        first = fake.runner("first")
        second = fake.runner("second", clone=fake.make_clone("other"))
        fake.claim_gate = Barrier(2)
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda runner: runner.claim({"number": 1, "labels": []}, "swarm/issue-1"), [first, second]))
        self.assertEqual(results.count(None), 1)
        self.assertIn(fake.head(1), results)
        fake.claim_gate = None
        fake.issue(1, swarm.READY)
        owner = first if results[0] else second
        loser = second if results[0] else first
        fake.issue(2, swarm.READY, body="### File scope\n- scripts/run.py")
        loser.poll()
        self.assertIsNone(loser.journal.last(2, "claim"))
        self.assertEqual(loser.journal.status()[0]["stage"], "foreign-claim")
        self.assertEqual(swarm.labels(fake.issues[1]), {swarm.READY})
        restarted = fake.runner(owner.journal.path.parent.parent.name, clone=owner.clone)
        restarted.poll()
        self.assertEqual(swarm.labels(fake.issues[1]), {swarm.ACTIVE})
        self.assertEqual(len(fake.comments[1]), 1)
        self.assertIn("Claimed by author", fake.comments[1][0]["body"])
        self.assertEqual(len(fake.pulls), 1)
        self.assertEqual(sum(call[:3] == ("git", "worktree", "add") for call in fake.calls), 1)

    def test_three_parallel_claims_reserve_scope_and_wait_for_dependencies(self):
        fake = self.fake
        fake.pulls[150] = {"number": 150, "state": "closed", "merged_at": None,
                           "head": {"ref": "other"}}
        for number, scope in {1: "packages/engine", 2: "packages/engine/src/example.py",
                              3: "packages/components", 4: "packages/environments",
                              5: "docs/a.md", 6: "docs/b.md"}.items():
            body = f"### File scope\n- {scope}"
            if number == 3:
                body += f"\n\n### Dependencies\n- [x] https://github.com/{swarm.REPO}/pull/150"
            fake.issue(number, swarm.READY, body=body)
        barrier = Barrier(3, timeout=10)
        runner = fake.runner()

        def verify_simultaneous(number, cwd):
            self.assertIn(number, {1, 4, 5})
            barrier.wait()
            status = subprocess.check_output([
                sys.executable, str(Path(swarm.__file__)), "author", "--status",
                "--state-dir", str(runner.state),
            ], text=True)
            self.assertEqual([json.loads(line)["issue"] for line in status.splitlines()], [1, 4, 5])

        fake.worker_hook = verify_simultaneous
        runner.poll()
        self.assertCountEqual([number for number, *_ in fake.worker_runs], [1, 4, 5])
        for number in (2, 3, 6):
            self.assertEqual(swarm.labels(fake.issues[number]), {swarm.READY})
            self.assertIsNone(runner.journal.last(number, "claim"))

        fake.worker_hook = None
        fake.pulls[100].update(state="closed", merged_at="now")
        fake.pulls[103].update(state="closed", merged_at="now")
        fake.pulls[150]["merged_at"] = "now"
        fake.runner().poll()
        self.assertEqual({number for number, *_ in fake.worker_runs}, {1, 2, 3, 4, 5})

    def test_author_stop_finishes_prepared_work_and_claims_no_more(self):
        fake = self.fake
        for number in (1, 2):
            fake.issue(number, swarm.READY, body=f"### File scope\n- result-{number}.txt")
        author = fake.runner()
        stopping = Event()
        author.stopping = stopping.is_set
        prepare = author.prepare

        def prepare_then_stop(number, active):
            job = prepare(number, active)
            stopping.set()
            return job

        with patch.object(author, "prepare", side_effect=prepare_then_stop):
            author.poll()
        self.assertEqual([number for number, *_ in fake.worker_runs], [1])
        self.assertIsNotNone(author.journal.last(1, "pr-open"))
        self.assertIsNone(author.journal.last(2, "claim"))
        fake.runner().poll()
        self.assertEqual([number for number, *_ in fake.worker_runs], [1, 2])

    def test_manual_completion_holds_dependent_once(self):
        fake = self.fake
        fake.issue(1, state="closed")
        fake.issues[1]["state_reason"] = "completed"
        fake.issue(2, body="### Dependencies\n- #1")
        runner = fake.runner()
        for _ in range(2):
            self.assertTrue(runner.pending_dependencies(fake.issues[2]))
        self.assertIn(swarm.HUMAN, swarm.labels(fake.issues[2]))
        posts = [c for c in fake.calls if c[:4] == ("gh", "api", "-X", "POST")]
        self.assertEqual(len(posts), 1)

    def test_completed_issue_requires_a_matching_merged_dev_pr(self):
        fake = self.fake
        fake.issue(1, state="closed")
        fake.issues[1]["state_reason"] = "completed"
        runner = fake.runner(dry=True)
        item = {"body": "### Dependencies\n- #1"}
        pr = {"state": "closed", "base": {"ref": "dev"}, "head": {"ref": "feature"},
              "body": "Fixes #1", "merged_at": None}
        fake.pulls[100] = pr
        self.assertTrue(runner.pending_dependencies(item))
        pr["body"] = "Resolves #1"
        pr["merged_at"] = "now"
        self.assertFalse(runner.pending_dependencies(item))
        pr["body"] = "Closes #10"
        self.assertTrue(runner.pending_dependencies(item))
        pr["head"]["ref"] = "swarm/issue-1"
        self.assertFalse(runner.pending_dependencies(item))
        pr["base"]["ref"] = "main"
        self.assertTrue(runner.pending_dependencies(item))

    def test_legacy_triage_backfills_scope(self):
        fake = self.fake
        fake.issue(1, swarm.LARGE, body="Legacy issue")
        runner = fake.runner(role=Issues)
        fingerprint = sha256(json.dumps(["Issue 1", "Legacy issue", []]).encode()).hexdigest()
        runner.journal.add(1, "triaged", fingerprint=fingerprint)
        runner.poll()
        self.assertIn("### File scope", fake.issues[1]["body"])

    def test_advisory_is_deduplicated_and_passes(self):
        fake = self.fake
        self.ready_prs(1)
        finding = {"title": "Improve wording", "evidence": "R3 and R12 ambiguous message", "impact": "Affects operators"}
        fake.findings["advisory_findings"] = [finding]
        fake.findings["advisory_findings"].append(finding)
        fake.runner(role=Swarm).poll()
        self.assertIn('"verdict": "PASS"', fake.comments[100][-1]["body"])
        self.assertTrue(any("Non-blocking: Improve wording" in c["body"] for c in fake.comments[100]))
        self.assertEqual(set(fake.issues), {1, 2})
        self.assertIn(swarm.READY, swarm.labels(fake.issues[2]))
        self.assertIn("Requirements: R3, R12", fake.issues[2]["body"])
        self.assertIn(finding["impact"], fake.issues[2]["body"])

    def test_multiline_command_blocks(self):
        for body in ("### Acceptance\n```sh\ntrue\nfalse\n```", "Command\n\n    true\n    false\n",
                     "Command\n\n```bash\ntrue\nfalse\n```"):
            self.assertEqual(swarm.accepted_commands(body), ["true", "false"])

    def test_outside_claim_scope_rejected(self):
        self.fake.issue(1, swarm.READY, body="### File scope\n- docs/a.md")
        self.fake.worker_hook = lambda number, cwd: (cwd / "outside").touch()
        self.fake.runner().poll()
        self.assertFalse(self.fake.pulls)
        self.assertIn("outside pinned claim scope: outside", str(self.fake.comments))

    def test_each_command_in_mixed_acceptance_section_is_required(self):
        self.ready_prs(1)
        self.fake.issues[1]["body"] = "### Acceptance\n`true`\n```sh\ntrue\n```\n`false`"
        self.commands.side_effect = lambda command, path: {
            "command": command, "exit_code": int(command == "false"), "output": ""}
        self.fake.runner(role=Swarm).poll()
        self.assertIn("false (exit 1)", self.fake.comments[100][-1]["body"])

    def test_no_acceptance_command(self):
        self.ready_prs(1)
        self.fake.issues[1]["body"] = ""
        self.fake.pulls[100]["body"] = "Closes #1"
        self.fake.runner(role=Swarm).poll()
        self.assertIn("no acceptance command", self.fake.comments[100][-1]["body"])
        self.assertIn('"verdict": "FAIL"', self.fake.comments[100][-1]["body"])

    def test_existing_pass_after_acceptance_changes(self):
        self.ready_prs(1)
        self.fake.runner(role=Swarm).poll()
        self.fake.issues[1]["body"] = "### Acceptance\n`false`"
        self.commands.side_effect = lambda command, path: {
            "command": command, "exit_code": int(command == "false"), "output": ""}
        self.fake.runner(role=Swarm).poll()
        self.assertIn("false (exit 1)", self.fake.comments[100][-1]["body"])

    def test_required_command_failure_overrides_model_pass(self):
        self.ready_prs(1)
        self.commands.side_effect = lambda command, path: {"command": command, "exit_code": 7, "output": "failed"}
        self.fake.runner(role=Swarm).poll()
        self.assertIn('"verdict": "FAIL"', self.fake.comments[100][-1]["body"])

    def test_command_result_runs_inside_snapshot_and_captures_stderr(self):
        runner = self.fake.runner(role=Swarm)
        command = "pwd >&2; exit 7"
        with patch("roles.OFFLINE", (command,)):
            result = SwarmTest.real_command_result(runner, command, self.fake.root)
        self.assertEqual((result["exit_code"], result["output"]), (7, str(self.fake.root)))

    def test_advisory_error_after_publication(self):
        self.ready_prs(1)
        self.fake.findings["advisory_findings"] = [{"title": "Style", "evidence": "example", "impact": "clarity"}]
        def fail(*args, **kwargs):
            self.assertIn('"verdict": "PASS"', self.fake.comments[100][-1]["body"])
            raise swarm.ToolError("unavailable")
        with patch.object(Swarm, "advisory_issue", side_effect=fail) as filing:
            self.fake.runner(role=Swarm).poll()
            filing.assert_called_once()
        self.fake.runner(role=Swarm).poll()
        self.fake.runner(role=Swarm).poll()
        self.assertEqual(sum("swarm-advisory:" in item["body"] for item in self.fake.issues.values()), 1)

    def test_advisory_defaults_to_pr_requirement(self):
        self.ready_prs(1)
        self.fake.pulls[100]["body"] += "\nRequirements: R8"
        self.fake.findings["advisory_findings"] = [{"title": "Style", "evidence": "example", "impact": "clarity"}]
        self.fake.runner(role=Swarm).poll()
        self.assertIn("Requirements: R8", self.fake.issues[2]["body"])

    def test_dependency_followup_errors_are_pending(self):
        runner = self.fake.runner(dry=True)
        item = {"body": "### Dependencies\n- #1"}
        closed = {"state": "closed", "state_reason": "completed"}
        for closure_fails in (True, False):
            response = (0, '{"data":{"repository":{"issue":{"timelineItems":{"nodes":[]}}}}}')
            with patch.object(runner, "api", side_effect=[closed, swarm.ToolError("merged lookup")]), patch.object(
                    runner, "call", side_effect=swarm.ToolError("closure lookup") if closure_fails else None,
                    return_value=response):
                self.assertIn("(unavailable)", runner.pending_dependencies(item)[0])

    def test_verified_spec_violation_outside_acceptance_blocks(self):
        fake = self.fake
        self.ready_prs(1)
        fake.findings["blocking_findings"] = [{"criterion": "R19 requires disjoint active claims",
            "title": "Missing test", "evidence": "Required assertion missing", "impact": "Coverage"}]
        fake.runner(role=Swarm).poll()
        self.assertIn('"verdict": "FAIL"', fake.comments[100][-1]["body"])

    def test_bare_closed_pr_requires_merge(self):
        runner = self.fake.runner()
        item = {"body": "### Dependencies\n- #150"}
        dependency = {"state": "closed", "state_reason": "completed", "pull_request": {"merged_at": None}}
        with patch.object(runner, "api", return_value=dependency):
            self.assertTrue(runner.pending_dependencies(item))
            dependency["pull_request"]["merged_at"] = "now"
            self.assertFalse(runner.pending_dependencies(item))

    def test_foreign_claim_keeps_original_scope_in_status_and_scheduler(self):
        fake = self.fake
        fake.issue(46, swarm.ACTIVE, body="### File scope\n- docs/")
        fake.runner("other").claim(fake.issues[46], "swarm/issue-46")
        fake.issues[46]["body"] = ""
        fake.issue(1, swarm.READY, body="### File scope\n- docs/a.md")
        fake.calls.clear()
        fake.runner(dry=True).poll()
        self.assertFalse(any(call[:2] == ("git", "fetch") for call in fake.calls))
        runner = fake.runner()
        runner.poll()
        self.assertFalse(fake.worker_runs)
        status = subprocess.check_output([sys.executable, swarm.__file__, "author", "--status",
                                          "--state-dir", str(runner.state)], text=True)
        self.assertEqual(json.loads(status), {"issue": 46, "scope": ["docs"], "stage": "foreign-claim"})

    def test_foreign_legacy_ref_reserves_a_slot_before_label_update(self):
        fake = self.fake
        fake.issue(46, swarm.READY, body="### File scope\n- docs/")
        head = git(fake.clone, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "Swarm claim #46 by legacy")
        git(fake.clone, "push", "origin", f"{head}:refs/heads/swarm/issue-46")
        for number, scope in {1: "packages/engine", 2: "docs/a.md", 3: "docs/b.md",
                              4: "docs/c.md", 5: "Unknown"}.items():
            fake.issue(number, swarm.READY, body=f"### File scope\n- {scope}")

        upgraded = fake.runner()
        upgraded.poll()
        self.assertEqual(fake.head(46), head)
        self.assertIsNone(upgraded.journal.last(46, "claim"))
        self.assertCountEqual([number for number, *_ in fake.worker_runs], [2, 3])
        self.assertTrue(swarm.scopes_overlap([], swarm.file_scope(fake.issues[5])))
        self.assertEqual(swarm.labels(fake.issues[46]), {swarm.READY})

    def test_skip_dependencies_restart_delayed_ci_and_unblock(self):
        fake = self.fake
        fake.issue(1, swarm.READY, "ml-review", body="## Acceptance\nRun the suite.\n\n### File scope\n- docs/1.md")
        fake.issue(2, swarm.READY, swarm.BLOCKED, body="## Dependencies\n- [ ] #1\n- [x] manual: confirmed\n\n### File scope\n- docs/2.md")
        fake.issue(3, swarm.READY, swarm.ACTIVE)
        fake.issue(4, swarm.READY, swarm.HUMAN)
        fake.issue(5, swarm.READY, body="## Dependencies\n#1\n\n## Acceptance\nUse the suite.\n\n### File scope\n- docs/5.md")
        fake.issue(6, swarm.BLOCKED)
        fake.issue(7, swarm.READY, body="## Dependencies\nExternal: maintainer confirmation")
        fake.drop_create_reply = True

        fake.runner(dry=True).poll()
        self.assertIn("#1: would claim", self.stdout.getvalue())
        self.assertIn("#5: wait for", self.stdout.getvalue())
        self.assertIn("#7: wait for", self.stdout.getvalue())
        self.assertTrue(all(call[:4] == ("gh", "api", "-X", "GET")
                            for call in fake.calls if call[0] != "git"))
        self.assertFalse(fake.runner(dry=True).journal.path.exists())

        fake.runner().poll()
        head = fake.head(1)
        self.assertEqual(len(fake.pulls), 1)
        self.assertEqual(len(fake.worker_runs), 1)
        self.assertNotIn("password=", fake.pulls[100]["body"])
        self.assertIn("Closes #1", fake.pulls[100]["body"])
        self.assertIn("Ran 1 test", fake.pulls[100]["body"])
        self.assertEqual(len(fake.comments[1]), 1)

        fake.runner().poll()
        self.assertEqual(fake.reviews, {})
        self.assertEqual(swarm.labels(fake.pulls[100]), {"ml-review"})
        self.assertEqual(len(fake.worker_runs), 1)
        self.assertEqual(fake.head(1), head)
        self.assertEqual(sum(call[:3] == ("git", "worktree", "add") for call in fake.calls), 1)

        fake.pulls[100].update(state="closed", merged_at="now", merged_by={"login": "maintainer"})
        restart = fake.runner()
        restart.poll()
        terminal = [e for e in restart.journal.events[1] if e["event"] == "terminal"]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0]["by"], "maintainer")
        self.assertEqual(fake.issues[1]["state"], "closed")
        self.assertEqual(swarm.labels(fake.issues[2]), {swarm.READY})
        self.assertEqual(swarm.labels(fake.issues[6]), {swarm.BLOCKED})
        self.assertEqual(len(fake.pulls), 2)  # Unblocked #5 was already ready when #1 closed.
        fake.runner().poll()
        self.assertEqual(sum(e["event"] == "terminal" for e in swarm.Journal(restart.journal.path).events[1]), 1)
        self.assertEqual(fake.worker_runs[0][0], 1)
        self.assertCountEqual([args[0] for args in fake.worker_runs[1:]], [2, 5])
        self.assertEqual(len(fake.pulls), 3)
        self.assertFalse(any("merge" in args for args in fake.calls))

    def test_merger_rebase_conflict_starts_author_repair_on_current_dev(self):
        fake = self.fake
        fake.worker_hook = lambda number, cwd: (cwd / "README.md").write_text("author\n")
        fake.issue(1, swarm.READY, body="### Acceptance\n`uv run python -m unittest`\n\n"
                   "### File scope\n- README.md\n- result-1.txt")
        fake.runner().poll()
        original = fake.head(1)
        other = fake.make_clone("new-dev")
        (other / "README.md").write_text("new dev\n")
        git(other, "commit", "-am", "Change the same file on dev")
        git(other, "push", "origin", "dev")
        base = git(fake.remote, "rev-parse", "dev")

        merger = fake.runner(role=Merger)
        merger.poll()
        bounce = merger.journal.last(100, "bounce")
        self.assertTrue(bounce["repair"])
        self.assertEqual(bounce["conflicts"], ["README.md"])
        self.assertIn("README.md", bounce["reason"])
        self.assertEqual(fake.head(1), original)

        def resolve(number, cwd):
            self.assertEqual(git(cwd, "rev-parse", "HEAD"), base)
            self.assertEqual(git(cwd, "diff", "--name-only", "--diff-filter=U"), "README.md")
            (cwd / "README.md").write_text("author and dev\n")

        fake.worker_hook = resolve
        author = fake.runner()
        def setup_before_conflict(path):
            self.assertFalse(git(path, "diff", "--name-only", "--diff-filter=U"))

        with patch.object(author, "prepare_workspace", side_effect=setup_before_conflict) as setup:
            author.poll()
        setup.assert_called_once()
        repaired = fake.head(1)
        self.assertIn("README.md", author.journal.last(1, "start")["findings"])
        self.assertIn("README.md", fake.worker_runs[-1][2])
        self.assertEqual(git(fake.remote, "rev-parse", repaired + "^"), base)
        self.assertEqual(git(fake.remote, "show", repaired + ":README.md"), "author and dev")
        self.assertIn(f"Acceptance at `{repaired}`", fake.pulls[100]["body"])
        message = git(fake.remote, "show", "-s", "--format=%B", repaired)
        original_scope = author.journal.last(1, "claim")["scope"]
        self.assertIn("Claim-Scope: " + (" ".join(original_scope) or "Unknown"), message.splitlines())
        provenance.check(fake.pulls[100]["body"], [{"sha": repaired, "commit": {"message": message}}])

        fake.runner().poll()
        self.assertEqual(len(fake.worker_runs), 2)
        self.assertEqual(len(fake.pulls), 1)
        self.assertEqual(fake.merge_runs, [])

    def test_dev_setup_failure_files_one_infrastructure_issue(self):
        fake = self.fake
        fake.setup_status = 1
        fake.runner(role=Swarm).poll()
        validator = fake.runner(role=Swarm)
        validator.poll()

        self.assertEqual(fake.validation_runs, [])
        self.assertIn(swarm.SETUP, fake.calls)
        self.assertEqual(len(fake.issues), 1)
        self.assertIn("Failed to download arro3-core",
                      fake.issues[1]["body"])
        self.assertEqual(validator.journal.last(0, "infra-failure")["head"], validator.dev())

    def test_parallel_pr_setup_failures_share_one_issue(self):
        self.ready_prs(1, 2)
        fake = self.fake
        fake.setup_status = 1
        validator = fake.runner(role=Swarm)
        validator.poll()
        for number in (0, 100, 101):
            self.assertIn("Failed to download", validator.journal.last(number, "infra-failure")["reason"])
        self.assertEqual(len(fake.issues), 3)
        self.assertEqual(fake.validation_runs, [])
        self.assertEqual(fake.statuses, {})

    def test_setup_retains_subprocess_stderr(self):
        result = subprocess.CompletedProcess(swarm.SETUP, 1, stdout="progress", stderr="download failed")
        with patch.object(subprocess, "run", return_value=result) as run:
            self.assertEqual(swarm.execute(list(swarm.SETUP), self.fake.clone), (1, "download failed"))
        self.assertEqual(run.call_args.kwargs["cwd"], self.fake.clone)
        self.assertNotIn("env", run.call_args.kwargs)

    def test_external_head_is_held_without_review(self):
        fake = self.fake
        fake.issue(1, swarm.READY)
        runner = fake.runner()
        runner.poll()
        original = fake.head(1)
        fake.external_push(1)
        runner.poll()
        self.assertEqual(fake.validation_runs, [])
        self.assertNotEqual(fake.head(1), original)
        self.assertEqual(runner.journal.terminal(1)["outcome"], "blocked")

    def test_author_repairs_only_a_matching_bounce_and_keeps_fix_cap(self):
        fake = self.fake
        fake.issue(1, swarm.READY)
        runner = fake.runner()
        runner.poll()
        original = fake.head(1)
        merger = fake.runner(role=Merger)
        merger.start(100, git(fake.remote, "rev-parse", "dev"), 1)
        merger.event("bounce", head="f" * 40, reason="Stale", repair=True)
        runner.poll()
        self.assertEqual(fake.head(1), original)
        for _ in range(3):
            head = fake.head(1)
            merger.event("bounce", head=head, reason="Verified failure.", repair=True)
            runner = fake.runner()
            runner.poll()
        self.assertEqual(swarm.labels(fake.issues[1]), {swarm.BLOCKED})
        self.assertIn("Fix limit exhausted", fake.comments[1][-1]["body"])
        self.assertEqual(len(fake.worker_runs), 3)
        self.assertEqual(len(fake.pulls), 1)
        self.assertEqual(fake.merge_runs, [])

    def test_foreign_prs_and_closed_issues_are_untouched(self):
        fake = self.fake
        fake.issue(1, swarm.READY, state="closed")
        fake.issue(2, swarm.READY)
        fake.issue(3, swarm.READY)
        fake.pulls[100] = {"number": 100, "head": {"ref": "chat/maintenance"},
                           "body": "Fixes debsagar/enterpriseengine#2", "state": "open"}
        fake.pulls[101] = {"number": 101, "head": {"ref": "swarm/issue-3", "sha": "external"},
                           "body": "", "state": "open"}
        fake.runner().poll()
        self.assertFalse(fake.worker_runs)
        self.assertFalse(any(call[:4] in (("gh", "api", "-X", "POST"), ("gh", "api", "-X", "PATCH"))
                             for call in fake.calls))

    def test_provenance_trailers_redaction_and_shared_log(self):
        fake = self.fake
        fake.issue(1, swarm.READY)
        fake.issue(2, swarm.READY, body="token=do-not-retain\n\n### File scope\n- docs/example.md")
        runner = fake.runner()
        fake.worker_hook = lambda number, cwd: (cwd / "PROJECT-LOG.jsonl").touch() if number == 1 else None
        runner.poll()
        self.assertEqual(runner.journal.terminal(1)["outcome"], "blocked")
        record = runner.record(2)
        pr = fake.pulls[101]
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
        self.assertIn(record["transcript"], fake.comments[101][0]["body"])
        self.assertEqual(swarm.dependencies("## Dependencies\n- [x] manual: done", unblocking=True),
                         (set(), False))
        self.assertEqual(swarm.redact({"access_token": "unmarked", "output": "OK"}), {"output": "OK"})
        self.assertEqual(swarm.dependencies("## Dependencies\n- [ ] manual: also #2"), (set(), True))

    def test_trace_joins_pr_and_journal(self):
        fake = self.fake
        fake.issue(1, swarm.READY)
        runner = fake.runner()
        runner.poll()
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
        owner.poll()
        new_runner = fake.runner()
        new_runner.runner_sha = "f" * 40
        new_runner.poll()
        self.assertIsNone(new_runner.journal.terminal(1))
        self.assertEqual(new_runner.journal.status()[0]["status"], "paused")
        self.assertEqual(new_runner.journal.status()[0]["runner"], owner.runner_sha)
        self.assertEqual(len(fake.worker_runs), 1)

    def ready_prs(self, *numbers):
        for number in numbers:
            self.fake.issue(number, swarm.READY, body=f"### Acceptance test command\n\n`uv run python -m unittest`\n\n### File scope\n- result-{number}.txt")
        self.fake.runner().poll()

    def test_role_permissions_and_swarm_verifies_pr_in_two_passes(self):
        fake = self.fake
        self.ready_prs(1)
        author_calls = [call for call in fake.calls if call[:5] ==
                        ("gh", "api", "-X", "POST", f"repos/{swarm.REPO}/pulls")]
        self.assertEqual(len(author_calls), 1)
        head = fake.head(1)
        for role in (Swarm, Merger, Issues):
            runner = fake.runner(role=role)
            with self.assertRaises(swarm.JobError):
                runner.api(f"{runner.repo}/pulls", method="POST", data={})
            if role is not Merger:
                with self.assertRaises(swarm.JobError):
                    runner.git("push", "--force-with-lease=refs/heads/topic:", "origin", f"{head}:refs/heads/topic")
            with self.assertRaises(swarm.JobError):
                runner.git("push", "--force-with-lease=refs/heads/dev:", "origin", f"{head}:refs/heads/dev")
        fake.calls.clear()
        fake.runner(role=Swarm).poll()
        self.assertEqual(fake.head(1), head)
        self.assertEqual([step[0] for step in fake.validation_runs], [0, 100, 100])
        self.assertEqual(fake.validation_runs[-1][2], "second pass")
        self.assertIn('"verdict": "PASS"', fake.comments[100][-1]["body"])
        self.assertEqual(set(fake.statuses[head]), set(swarm.VERDICT_CONTEXTS.values()))
        self.assertEqual(fake.reviews, {})
        self.assertFalse(any(call[:2] == ("git", "push") for call in fake.calls))

    def test_parallel_validation_uses_distinct_checkouts_and_verdicts(self):
        self.ready_prs(1, 2)
        fake = self.fake
        validator = fake.runner(role=Swarm)
        barrier = Barrier(2, timeout=10)

        def overlap(number, cwd):
            self.assertEqual(cwd, validator.validation_path(number))
            barrier.wait()
            self.assertTrue(validator.validation_path(100).is_dir())
            self.assertTrue(validator.validation_path(101).is_dir())
            barrier.wait()

        fake.validation_hook = overlap
        validator.poll()
        for number, issue in ((100, 1), (101, 2)):
            verdict = validator.journal.last(number, "swarm-verdict")
            self.assertEqual((verdict["head"], verdict["base"]), (fake.head(issue), validator.dev()))
            self.assertEqual(fake.statuses[fake.head(issue)]["swarm/validate"]["state"], "success")
            self.assertFalse(validator.validation_path(number).exists())

    def test_stop_drains_running_validation_and_leaves_next_pr_unclaimed(self):
        self.ready_prs(1, 2)
        fake = self.fake
        validator = fake.runner(role=Swarm)
        stopping = Event()
        validator.stopping = stopping.is_set

        def request_stop(number, cwd):
            self.assertEqual(number, 100)
            stopping.set()

        fake.validation_hook = request_stop
        with patch("roles.WORKER_SLOTS", 1):
            validator.poll()
        journal = swarm.Journal(validator.journal.path)
        claim = journal.last(100, "validation-claim")
        self.assertEqual(journal.last(100, "validation-release")["claim"], claim["claim"])
        self.assertIsNone(journal.last(101, "validation-claim"))
        self.assertFalse(validator.validation_path(100).exists())
        self.assertEqual(set(fake.statuses[fake.head(1)]), set(swarm.VERDICT_CONTEXTS.values()))

    def test_restart_reconciles_checkout_and_interrupted_status_post(self):
        self.ready_prs(1)
        fake = self.fake
        validator = fake.runner(role=Swarm)
        fake.findings["advisory_findings"] = [{"title": "Style", "evidence": "example", "impact": "clarity"}]
        api = fake.api

        def fail_status(method, target, data):
            if target.endswith(f"/statuses/{fake.head(1)}"):
                return 1, ""
            return api(method, target, data)

        with patch.object(fake, "api", side_effect=fail_status):
            validator.poll()
        journal = validator.journal
        intent = journal.last(100, "swarm-publication")
        self.assertIsNone(journal.last(100, "swarm-verdict"))
        self.assertEqual(len(fake.comments[100]), 2)  # Author provenance and verdict comment.
        journal.add(100, "validation-claim", claim="interrupted", head=intent["head"], base=intent["base"])
        path = validator.validation_path(100)
        git(fake.clone, "worktree", "add", "--detach", str(path), intent["head"])
        fake.validation_runs.clear()

        restarted = fake.runner(role=Swarm)
        restarted.poll()
        self.assertEqual([number for number, *_ in fake.validation_runs], [0])
        verdict = restarted.journal.last(100, "swarm-verdict")
        self.assertEqual(verdict["provenance"], intent["provenance"])
        self.assertIn("swarm-advisory:", fake.issues[2]["body"])
        self.assertFalse(path.exists())
        self.assertTrue(any(event.get("status") == "interrupted" and event.get("claim") == "interrupted"
                            for event in restarted.journal.events[100]))
        self.assertEqual(sum("swarm-verdict" in comment["body"] and "Swarm validation" in comment["body"]
                             for comment in fake.comments[100]), 1)

    def test_merger_rejects_same_head_verdict_when_dev_moves(self):
        self.ready_prs(1)
        fake = self.fake
        merger = fake.runner(role=Merger)
        merger.poll()
        head, base = fake.head(1), merger.dev()
        fake.ci(head)
        fake.runner(role=Swarm).poll()
        merger.login = "bot"
        pr = fake.pull(fake.pulls[100])
        self.assertEqual(merger.gate(pr, head, base)[0], "ready")

        new_base = git(fake.clone, "commit-tree", f"{base}^{{tree}}", "-p", base, "-m", "Move dev")
        git(fake.clone, "push", "origin", f"{new_base}:refs/heads/dev")
        self.assertEqual(merger.gate(pr, head, new_base)[0], "waiting for swarm/validate")
        self.assertEqual(merger.gate(pr, head, base)[0], "candidate changed or held")
        merger.poll()
        self.assertEqual(fake.merge_runs, [])
        self.assertNotEqual(fake.head(1), head)

    def test_norvale_baseline_runs_when_tracked(self):
        fake = self.fake
        script = fake.clone / "scripts" / "norvale_dry_run.py"
        script.parent.mkdir()
        script.write_text("# test fixture")
        git(fake.clone, "add", str(script))
        git(fake.clone, "commit", "-m", "Add fixture dry run")
        git(fake.clone, "push", "origin", "dev")
        fake.runner(role=Swarm).poll()
        self.assertTrue(any("norvale_dry_run.py --days 30 --compare" in command
                            for command in fake.validation_runs[0][2]))

    def test_merger_lock_serial_rebase_ci_and_independent_review(self):
        fake = self.fake
        self.ready_prs(1, 2)
        with patch.object(swarm, "STATE", fake.root / "locks"):
            with swarm.role_lock("merger"), swarm.role_lock("swarm"):
                with self.assertRaisesRegex(swarm.JobError, "holds the lock"):
                    with swarm.role_lock("merger"):
                        self.fail("second merger acquired the lock")

        old1, old2 = fake.head(1), fake.head(2)
        fake.runner(role=Swarm).poll()
        self.assertEqual(set(fake.statuses[old1]), set(swarm.VERDICT_CONTEXTS.values()))
        fake.runner(role=Merger).poll()
        head = fake.head(1)
        self.assertNotEqual(head, old1)
        self.assertEqual(fake.head(2), old2)
        self.assertIn("Swarm-Rebase-Model: gpt-6-luna",
                      git(fake.remote, "show", "-s", "--format=%B", head))
        record = provenance.parse(fake.pulls[100]["body"])
        commits = [{"sha": sha, "commit": {"message": git(fake.remote, "show", "-s", "--format=%B", sha)}}
                   for sha in git(fake.remote, "rev-list", f"{record['base']}..{head}").splitlines()]
        provenance.check(fake.pulls[100]["body"], commits)

        fake.runner(role=Merger).poll()
        self.assertEqual(fake.merge_runs, [])
        fake.ci(head)
        fake.runner(role=Merger).poll()
        merger = fake.runner(role=Merger)
        self.assertEqual(merger.journal.last(100, "decision")["status"], "waiting for swarm/validate")
        self.assertEqual(fake.merge_runs, [])
        fake.runner(role=Swarm).poll()
        review_status = fake.statuses[head].pop("review/independent")
        merger.poll()
        self.assertEqual(merger.journal.last(100, "decision")["status"], "waiting for review/independent")
        self.assertEqual(fake.merge_runs, [])
        fake.statuses[head]["review/independent"] = review_status
        self.assertEqual(fake.head(2), old2)

        fake.strict = False
        merger = fake.runner(role=Merger)
        merger.poll()
        self.assertEqual(fake.merge_runs, [])
        self.assertIn("strict required checks", merger.journal.last(100, "decision")["status"])
        fake.strict = True
        merger.poll()
        self.assertEqual(fake.merge_runs, [100])
        self.assertEqual(fake.head(2), old2)
        self.assertTrue((merger.journal.path.parent / "reports" / f"pr-100-{head}.md").is_file())
        fake.runner(role=Merger).poll()
        second = fake.head(2)
        self.assertNotEqual(second, old2)
        self.assertEqual(git(fake.remote, "merge-base", head, second), head)

    def test_verdict_comment_must_name_the_current_head(self):
        fake = self.fake
        self.ready_prs(1)
        validator = fake.runner(role=Swarm)
        validator.poll()
        head, base = fake.head(1), validator.dev()
        review = fake.comments[100][-1]
        marker, rest = review["body"].split("\n", 1)
        review["body"] = marker.replace(head, "f" * 40) + "\n" + rest
        merger = fake.runner(role=Merger)
        merger.login = "bot"
        self.assertEqual(merger.gate(fake.pull(fake.pulls[100]), head, base)[0], "waiting for review/independent")

    def test_ml_review_stops_queue(self):
        fake = self.fake
        self.ready_prs(1, 2)
        head, other = fake.head(1), fake.head(2)
        fake.pulls[100]["labels"] = [{"name": "ml-review"}]
        fake.ci(head)
        fake.runner(role=Swarm).poll()
        for _ in range(2):
            fake.runner(role=Merger).poll()
        self.assertEqual(swarm.labels(fake.pulls[100]), {"ml-review", swarm.HUMAN})
        self.assertEqual((fake.head(1), fake.head(2)), (head, other))
        self.assertEqual(fake.merge_runs, [])

    def test_author_sizes_pin_model_effort_and_triage_normalizes_labels(self):
        fake = self.fake
        fake.issue(1, swarm.READY, swarm.SMALL, body="Reasoning effort: high\n\n### File scope\n- result-1.txt")
        fake.issue(2, swarm.READY, swarm.LARGE, body="ReasoningEffort: xhigh\n\n### File scope\n- result-2.txt")
        fake.issue(3, swarm.READY, body="### File scope\n- result-3.txt")
        fake.issue(4, swarm.SMALL, swarm.LARGE)
        fake.runner().poll()
        self.assertCountEqual(fake.author_calls, [
            (1, "gpt-6-luna", "medium"), (2, "gpt-5.6-terra", "xhigh"), (3, "gpt-5.6-terra", "medium"),
        ])
        for number, model, effort in fake.author_calls:
            record = provenance.parse(fake.pulls[99 + number]["body"])
            self.assertEqual((record["model"], record["effort"]), (model, effort))
        fake.issue_plans[4] = {"size": "small", "classification": "distinct", "duplicate": "",
                               "impact": "Scoped work.", "prerequisites": "",
                               "scope": "packages/engine/\ndocs/release.md"}
        fake.runner(role=Issues).poll()
        self.assertEqual(swarm.labels(fake.issues[4]), {swarm.SMALL})
        self.assertEqual(swarm.file_scope(fake.issues[4]), ["docs/release.md", "packages/engine"])
        self.assertIn(swarm.LARGE, swarm.labels(fake.issues[3]))

    def test_review_eligibility_and_same_model_verdict_rejected(self):
        fake = self.fake
        self.assertEqual(eligible_reviewers("gpt-6-luna"), ("claude-opus-5-5", "gpt-5.6-terra"))
        self.assertEqual(eligible_reviewers("gpt-5.6-terra"), ("claude-opus-5-5",))
        self.assertEqual(eligible_reviewers(None), ())
        self.ready_prs(1)
        validator = fake.runner(role=Swarm)
        validator.poll()
        head, base = fake.head(1), validator.dev()
        valid = validator.journal.last(100, "reviewer-verdict")
        source = {**valid["provenance"], "model": "gpt-5.6-terra", "provider": "OpenAI",
                  "effort": "medium", "reviewer_fallback": True, "fallback_reason": "quota"}
        validator.journal.add(100, "reviewer-verdict", head=head, base=base, provenance=source, passed=True)
        fake.comments[100][-1]["body"] = ("<!-- swarm-verdict " +
                                         json.dumps(verdict_record(source, head, True)) + " -->\n" +
                                         provenance.block(source))
        merger = fake.runner(role=Merger)
        merger.login = "bot"
        self.assertEqual(merger.gate(fake.pull(fake.pulls[100]), head, base)[0], "waiting for review/independent")

    def test_claude_fallback_and_astra_wait_does_not_block_other_prs(self):
        fake = self.fake
        for number, size in ((1, swarm.LARGE), (2, swarm.SMALL)):
            fake.issue(number, swarm.READY, size, body=f"### Acceptance test command\n\n`uv run python -m unittest`\n\n### File scope\n- result-{number}.txt")
        fake.runner().poll()
        fake.runner(role=Merger).poll()
        fake.claude_errors = {100: "401 authentication failed", 101: "429 rate limit exceeded"}
        with patch("roles.sleep") as wait:
            fake.runner(role=Swarm).poll()
        self.assertCountEqual([call.args[0] for call in wait.call_args_list], [2, 4, 2, 4])
        self.assertCountEqual(fake.review_calls, [(100, "claude-opus-5-5", "low")] * 3 +
                         [(101, "claude-opus-5-5", "low")] * 3 + [(101, "gpt-5.6-terra", "medium")])
        self.assertIn(swarm.AWAITING_REVIEW, swarm.labels(fake.pulls[100]))
        self.assertNotIn("review/independent", fake.statuses[fake.head(1)])
        record = provenance.parse(fake.comments[101][-1]["body"])
        self.assertTrue(record["reviewer_fallback"])
        self.assertEqual(record["fallback_reason"], "Claude quota or rate limit")
        merger = fake.runner(role=Merger)
        merger.poll()
        self.assertEqual(merger.journal.last(100, "release")["status"], "awaiting-review")
        merger.poll()
        head = fake.head(2)
        fake.ci(head)
        with patch("roles.sleep"):
            fake.runner(role=Swarm).poll()
        merger.poll()
        self.assertEqual(fake.merge_runs, [101])
        self.assertIn("Fallback review", (merger.journal.path.parent / "reports" / f"pr-101-{head}.md").read_text())
        del fake.claude_errors[100]
        fake.runner(role=Swarm).poll()
        self.assertNotIn(swarm.AWAITING_REVIEW, swarm.labels(fake.pulls[100]))
        self.assertEqual(fake.statuses[fake.head(1)]["review/independent"]["state"], "success")

    def test_ci_baseline_defers_and_author_can_repair_rebased_head(self):
        fake = self.fake
        self.ready_prs(1)
        merger = fake.runner(role=Merger)
        merger.poll()
        head = fake.head(1)
        dev = git(fake.remote, "rev-parse", "dev")
        fake.ci(dev)
        fake.ci(head)
        fake.checks[dev]["Offline workspace tests"] = "failure"
        fake.checks[head]["Offline workspace tests"] = "failure"
        merger.poll()
        self.assertEqual(merger.journal.last(100, "decision")["status"], "infra-failure")
        self.assertIsNone(merger.journal.last(100, "bounce"))
        fake.ci(dev)
        merger.poll()
        self.assertEqual(merger.journal.last(100, "bounce")["head"], head)
        fake.runner().poll()
        self.assertNotEqual(fake.head(1), head)
        self.assertIn("CI failed", fake.worker_runs[-1][2])
        self.assertEqual(len(fake.pulls), 1)

    def test_issue_dedupe_preserves_related_work_and_redirects_dependencies(self):
        fake = self.fake
        fake.issue(1, body="### Goal\n\nShared underlying problem.\n\n### Dependencies\n\nNone")
        fake.issue(2, body="### Goal\n\nDuplicate report.\n\n### Dependencies\n\n- [x] manual: verified")
        fake.issue(3, body="### Goal\n\nRelated work.\n\n### Dependencies\n\n- [ ] #2")
        fake.issue(4, body="### Goal\n\nDownstream consumer.\n\nDeps: None")
        fake.issue_plans[2] = {"classification": "match", "duplicate": "#1", "impact": "Same fix.",
                               "prerequisites": ""}
        fake.issue_plans[3] = {"classification": "similar", "duplicate": "#1", "impact": "Shares a component.",
                               "prerequisites": ""}
        fake.issue_plans[4] = {"classification": "distinct", "duplicate": "", "impact": "Uses #3.",
                               "prerequisites": "#3"}
        fake.runner(role=Issues).poll()
        self.assertEqual(fake.issues[2]["state"], "closed")
        self.assertIn("#1", fake.comments[2][-1]["body"])
        self.assertIn("#2", fake.comments[1][-1]["body"])
        self.assertEqual(fake.issues[3]["state"], "open")
        self.assertIn("### Downstream impact", fake.issues[3]["body"])
        self.assertEqual(swarm.dependencies(fake.issues[3]["body"]), ({(swarm.REPO, "issues", 1)}, False))
        self.assertIn("- [x] manual: verified", fake.issues[1]["body"])
        self.assertEqual(swarm.dependencies(fake.issues[4]["body"]), ({(swarm.REPO, "issues", 3)}, False))

    def test_human_template_and_no_issue_reason(self):
        record = {"actor": "human", "issue": "none", "issue_reason": "Routine cleanup", "base": "a" * 40}
        commits = [{"sha": "b" * 40, "commit": {"message": provenance.commit_message("Cleanup", record)}}]
        provenance.check(provenance.block(record), commits)
        with self.assertRaisesRegex(ValueError, "issue_reason"):
            provenance.check(provenance.block({**record, "issue_reason": ""}), commits)


if __name__ == "__main__":
    unittest.main()
