"""Provenance format, commit trailers and GitHub metadata gate."""

import json
from pathlib import Path
import re
import subprocess
import sys


CHECK = "PR provenance"
MARKER = "swarm-provenance"
BLOCK = re.compile(r"^<!-- " + MARKER + r"\n(.*?)\n-->$", re.M | re.S)
SHA = re.compile(r"[0-9a-f]{40}")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
ISSUE = re.compile(r"(?:[\w.-]+/[\w.-]+)?#[1-9][0-9]*")
TRAILERS = {
    "actor": "Change-Actor", "issue": "Issue", "base": "Base-SHA",
    "provider": "Agent-Provider", "model": "Agent-Model", "effort": "Agent-Effort",
    "run": "Agent-Run", "runner": "Runner", "reviewer": "Reviewer-Model",
}


def block(record):
    return f"<!-- {MARKER}\n{json.dumps(record, sort_keys=True)}\n-->"


def parse(body):
    matches = BLOCK.findall(body or "")
    if len(matches) != 1 or len(matches[0]) > 4096:
        raise ValueError("Supply one provenance block of at most 4096 characters.")
    data = json.loads(matches[0])
    if not isinstance(data, dict):
        raise ValueError("Provenance must be a JSON object.")
    return data


def validate(record):
    actor = record.get("actor")
    if actor not in ("human", "agent"):
        raise ValueError("Set actor to human or agent.")
    issue = record.get("issue")
    if not isinstance(issue, str) or not ISSUE.fullmatch(issue):
        if issue != "none" or not text_field(record, "issue_reason", 256):
            raise ValueError("Supply an issue reference or explain issue: none in issue_reason.")
    if not SHA.fullmatch(str(record.get("base", ""))):
        raise ValueError("Supply the full original base SHA.")
    if actor == "agent":
        for key in ("provider", "model", "effort", "run", "runner", "reviewer"):
            value = record.get(key)
            if not isinstance(value, str) or not TOKEN.fullmatch(value) or value == "REPLACE":
                raise ValueError(f"Supply agent provenance field {key}.")
        if record["runner"] != "external" and not SHA.fullmatch(record["runner"]):
            raise ValueError("Supply the pinned runner SHA or use external for chat agents.")
        if not text_field(record, "transcript", 512):
            raise ValueError("Supply a retained transcript locator.")
    return record


def text_field(record, key, limit):
    value = record.get(key)
    return isinstance(value, str) and 0 < len(value.strip()) <= limit and value == value.strip() and value.isprintable()


def trailers(record):
    return {label: record[key] for key, label in TRAILERS.items()
            if key in ("actor", "issue", "base") or record["actor"] == "agent"}


def commit_message(subject, record):
    footer = "\n".join(f"{key}: {value}" for key, value in trailers(record).items())
    return f"{subject}\n\n{footer}"


def check(body, commits, *, shared_log=False):
    record = validate(parse(body))
    if shared_log and record["actor"] == "agent":
        raise ValueError("Agent PRs cannot change PROJECT-LOG.jsonl.")
    expected = trailers(record)
    if not commits:
        raise ValueError("No PR commits returned.")
    for commit in commits:
        message = commit["commit"]["message"]
        parsed = subprocess.run(["git", "interpret-trailers", "--parse"], input=message,
                                capture_output=True, text=True, check=True).stdout
        for key, value in expected.items():
            actual = [line.split(": ", 1)[1] for line in parsed.splitlines()
                      if line.startswith(key + ": ")]
            if actual != [value]:
                raise ValueError(f"Commit {commit['sha'][:12]} needs the matching {key} trailer.")


def main():
    import os

    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    trigger = event["pull_request"]
    endpoint = f"repos/{os.environ['GITHUB_REPOSITORY']}/pulls/{trigger['number']}"
    pr = json.loads(subprocess.check_output(["gh", "api", endpoint], text=True))

    def read(suffix):
        output = subprocess.check_output(["gh", "api", "--paginate",
                                          endpoint + suffix + "?per_page=100", "--jq", ".[]"], text=True)
        return [json.loads(line) for line in output.splitlines()]

    try:
        if pr["head"]["sha"] != trigger["head"]["sha"] or pr["base"]["ref"] != trigger["base"]["ref"]:
            raise ValueError("The pull request changed while this run was queued.")
        commits = read("/commits")
        if len(commits) != pr["commits"]:
            raise ValueError("Cannot validate an incomplete PR commit list. Split large PRs.")
        # The compare API limits file results to 3000. Fail closed at that boundary.
        files = read("/files")
        if len(files) >= 3000:
            raise ValueError("Split PRs with 3000 or more files.")
        check(pr["body"], commits, shared_log=any(
            "PROJECT-LOG.jsonl" in (item["filename"], item.get("previous_filename")) for item in files
        ))
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    print("PR metadata and commit trailers match.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
