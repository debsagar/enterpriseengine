#!/usr/bin/env python3
"""Print a single PR timeline without dumping the retained transcript."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re

import provenance
from run import CLONE, Journal, REPO, STATE, WORKTREES, Runner, ToolError, safe_evidence


def timeline(api, target, state):
    repo = f"repos/{REPO}"
    if re.fullmatch(r"#?[1-9][0-9]*", target) and (target.startswith("#") or len(target) < 7):
        number = int(target.lstrip("#"))
    else:
        if not re.fullmatch(r"[0-9a-f]{7,40}", target):
            raise ValueError("Use #PR_NUMBER or a 7–40 character commit SHA.")
        commit = api(f"{repo}/commits/{target}")
        sha = commit["sha"]
        pulls = [pr for page in api(f"{repo}/commits/{sha}/pulls?per_page=100", pages=True) for pr in page]
        exact = [pr for pr in pulls if sha in (pr.get("merge_commit_sha"), pr["head"]["sha"])]
        matches = exact or pulls
        if len(matches) != 1:
            choices = ", ".join(f"#{pr['number']}" for pr in matches) or "none"
            raise ValueError(f"Commit has no unique PR ({choices}). Use a PR number.")
        number = matches[0]["number"]

    endpoint = f"{repo}/pulls/{number}"
    pr = api(endpoint)
    try:
        record = provenance.validate(provenance.parse(pr["body"]))
    except ValueError:
        record = {}
    events = []

    def add(timestamp, message):
        if timestamp:
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).isoformat()
            events.append((timestamp, message))

    add(pr["created_at"], f"PR opened by {pr['user']['login']}")
    if pr.get("merged_at"):
        add(pr["merged_at"], f"merged by {(pr.get('merged_by') or {}).get('login', '?')} "
                             f"as {pr.get('merge_commit_sha', '?')}")
    else:
        add(pr.get("closed_at"), "PR closed")
    head = pr["head"]["sha"]
    for page in api(endpoint + "/commits?per_page=100", pages=True):
        for item in page:
            commit = item["commit"]
            who = (item.get("author") or {}).get("login") or commit["author"]["name"]
            add(commit["committer"]["date"], f"commit {item['sha'][:12]} by {who}")
    for page in api(endpoint + "/reviews?per_page=100", pages=True):
        for review in page:
            verdict = re.match(r"Swarm verdict (APPROVE|REQUEST_CHANGES)", review["body"] or "")
            add(review["submitted_at"], f"review {verdict[1] if verdict else review['state']} "
                                        f"by {review['user']['login']} at {review['commit_id'][:12]}")
    for page in api(f"{repo}/commits/{head}/check-runs?per_page=100", pages=True):
        for check in page["check_runs"]:
            add(check["completed_at"] or check["started_at"],
                f"CI {check['name']}: {check['conclusion'] or check['status']} at {head[:12]}")
    for page in api(f"{repo}/commits/{head}/statuses?per_page=100", pages=True):
        for check in page:
            add(check["created_at"], f"CI {check['context']}: {check['state']} at {head[:12]}")

    run = record.get("run")
    journal = Journal(state / "log.jsonl")
    found = False
    if run:
        for entries in journal.events.values():
            for event in entries:
                if event.get("run") != run:
                    continue
                found = True
                kind = event["event"]
                details = " ".join(f"{key}={event[key]}" for key in
                                   ("worker", "round", "attempt", "call", "model", "sha", "status", "outcome")
                                   if event.get(key) is not None)
                add(event["ts"], f"{kind} {details}".rstrip())
    lines = [f"PR #{number} {pr['state']} head {head}"]
    if record:
        lines.extend(f"{key} {record[key]}" for key in
                     ("issue", "actor", "provider", "model", "effort", "reviewer", "runner",
                      "base", "run", "transcript") if key in record)
    else:
        lines.append("No valid provenance block (GitHub history only).")
    if run:
        path = state / "attempts" / f"{run}.jsonl"
        if path.is_file():
            retained = []
            with path.open("rb") as file:
                for line in file:
                    if not line.endswith(b"\n"):
                        break
                    event = json.loads(line)
                    if event.get("event") == "model-input":
                        retained.append(event.get("model"))
            lines.append(f"Local run record present ({len(retained)} captured model calls).")
        if not found:
            lines.append("No matching local journal events. Use the retained record locator.")
    lines.extend(f"{when} {safe_evidence(message, 500) or '[redacted]'}"
                 for when, message in sorted(events))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", metavar="PR#|SHA")
    parser.add_argument("--state-dir", type=Path, default=STATE)
    args = parser.parse_args()
    try:
        runner = Runner(CLONE, WORKTREES, Journal(args.state_dir / "log.jsonl"), dry=True,
                        runner_sha="trace")
        print(timeline(runner.api, args.target, args.state_dir))
    except (ToolError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
