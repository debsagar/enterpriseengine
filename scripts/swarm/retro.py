"""R19/R20 milestone retrospectives. Only the Author publishes repository changes."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from difflib import unified_diff
from hashlib import sha256
import re
from statistics import mean

from roles import author_model, linked_issue
from run import Journal, JobError, safe_evidence
from trace import failure_causes

VERSION = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
REQ = re.compile(r"\bR(?:20|1\d|[1-9])\b")
CYCLE = "Mean PR open-to-merge hours"
BOUNCE = "Bounced PRs (%)"
INFRA = "CI infra failure (%)"
PR = "CI PR failure (%)"
FALLBACK = "Reviewer fallback (%)"
WAIT = "Claude-unavailable wait (hours)"
REGRESS = "Swarm regressions"
REVERT = "Merged reverts"
MISSING = "Issues without requirement ID"
DRY = "#39 dry-run differences"
AUTO = "AutoTwin comparison deltas"
METRICS = [CYCLE, BOUNCE, INFRA, PR, FALLBACK, WAIT, REGRESS, REVERT, MISSING, DRY, AUTO]

# Ordered, bounded experiments. Each targets a measured failure mode.
EXPERIMENTS = {
    INFRA: "Add a dev baseline preflight before queuing a candidate",
    PR: "Run the failing required CI context before author handoff",
    MISSING: "Check requirement IDs during Issue-role triage",
}
GATES = [
    ("R12 R3", "Trace the TK-101 pause and continue branches through lot and order after restart"),
    ("R7 R11", "Run the component fit check on Norvale and a second company"),
    ("R17 R13", "Compare both companies' no-model runs with the baseline and AutoTwin"),
    ("R8", "Verify the catalog includes every registered entity type"),
]


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def version(name):
    match = VERSION.fullmatch(name)
    if not match:
        raise JobError("Use a milestone version such as v0.3.0.")
    return tuple(map(int, match.groups()))


def rate(count, total):
    return 100 * count / total if total else None


def public(value):
    return safe_evidence(value or "", 300).replace("\n", " ").replace("<!--", "&lt;!--")


def measure(client, issues, pulls, now):
    merger = Journal(client.state / "merger/history.jsonl")
    swarm = Journal(client.state / "swarm/log.jsonl")
    cycles, waits, diffs, deltas, causes = [], [], [], [], []
    authors, bounces, ci = Counter(), Counter(), Counter()
    reviews = fallbacks = reverts = 0
    for p in pulls:
        n = p["number"]
        if p.get("merged_at"):
            cycles.append((instant(p["merged_at"]) - instant(p["created_at"])).total_seconds() / 3600)
            reverts += bool(re.match(r"revert\b", p["title"], re.I))
        model = author_model(p) or "unattributed"
        authors[model] += 1
        events = merger.events.get(n, [])
        if any(e["event"] == "bounce" for e in events):
            bounces[model] += 1
        # One observation per candidate and outcome, independent of poll frequency.
        ci.update(status for _, _, status in {
            (e.get("head"), e.get("base"), e.get("status")) for e in events
            if e["event"] == "decision" and e.get("status") in {"failure", "infra-failure", "success"}
        })
        causes.extend(f"PR #{n} — {reason}" for reason in failure_causes(events))
        start = None
        dev_checks = [e for e in swarm.events.get(0, []) if e["event"] == "verdict" and
                      p.get("merge_commit_sha") and e.get("head") == p["merge_commit_sha"]]
        for e in [*swarm.events.get(n, []), *dev_checks]:
            kind = e["event"]
            if kind == "claude-unavailable" and start is None:
                start = instant(e["ts"])
            if kind == "reviewer-verdict":
                reviews += 1
                fallbacks += e["provenance"].get("reviewer_fallback") is True
                if start is not None:
                    waits.append((instant(e["ts"]) - start).total_seconds())
                    start = None
            if kind in {"swarm-verdict", "verdict"}:
                for cmd in e.get("acceptance", {}).get("commands", []):
                    command = cmd.get("command", "")
                    if "norvale_dry_run.py" in command and "--compare" in command:
                        diff = "matched" if cmd["exit_code"] == 0 else "comparison failed"
                        diffs.append(f"#{n} {diff} {public(cmd.get('output'))}".rstrip())
                    if "autotwin" in command.lower() and "delta" in cmd.get("output", "").lower():
                        deltas.append(f"#{n} {public(cmd['output'])}")
        if start is not None:
            end = instant(p["closed_at"]) if p.get("closed_at") else now
            waits.append(max(0, (end - start).total_seconds()))
    metrics = {
        CYCLE: mean(cycles) if cycles else None,
        BOUNCE: rate(sum(bounces.values()), len(pulls)) if merger.path.exists() else None,
        INFRA: rate(ci["infra-failure"], ci.total()), PR: rate(ci["failure"], ci.total()),
        FALLBACK: rate(fallbacks, reviews), WAIT: sum(waits) / 3600 if waits or reviews else None,
        REGRESS: sum("<!-- swarm-regression:" in (i.get("body") or "") for i in issues),
        REVERT: reverts, MISSING: sum(not requirements(i) for i in issues),
        DRY: ", ".join(dict.fromkeys(diffs)) or None, AUTO: ", ".join(dict.fromkeys(deltas)) or None,
    }
    for model in sorted(authors):
        metrics[f"{BOUNCE} / {model}"] = rate(bounces[model], authors[model]) if merger.path.exists() else None
    for item in issues:
        body = item.get("body") or ""
        if body.startswith("<!-- swarm-regression:"):
            # The verified Swarm report follows the dev anchor, before provenance.
            details = re.search(r"Dev `[a-f0-9]{40}`\s+(.*?)(?:\n<!--|\Z)", body, re.S)
            diagnosis = public(details[1]) if details else "Root cause unresolved in the recorded report."
            causes.append(f"Swarm finding #{item['number']} — {diagnosis}")
    return metrics, causes


def requirements(item):
    return sorted(set(REQ.findall(item["title"] + " " + (item.get("body") or ""))), key=lambda s: int(s[1:]))


def display(value):
    if value is None:
        return "unavailable"
    return f"{value:.2f}" if isinstance(value, float) else str(value).replace("|", r"\|")


def pages(client, suffix):
    return [item for page in client.api(f"{client.repo}/{suffix}", pages=True) for item in page]


def document_plan(path, name, target, metrics, previous, shipped, causes, tasks, outcomes):
    finish = (path / "SPEC.md").read_text().partition("## 6. v1.0 finish line")[2].strip()
    if not finish:
        raise JobError("SPEC section 6 is missing.")
    report = [f"# {name} retrospective", "", "## What shipped", "", *(shipped or ["No confirmed deliveries."]),
              "", "## Failures and recorded causes", "", *(causes or ["No attributed cause in available evidence."]),
              "", "## Metrics", "", f"| Metric | {name} | Previous milestone |", "|---|---:|---:|"]
    for key in dict.fromkeys([*metrics, *previous]):
        report.append(f"| {key} | {display(metrics.get(key))} | {display(previous.get(key))} |")
    steps = []
    for t in tasks:
        ref = f"#{t['number']} " if "number" in t else ""
        depends = f" Depends on #{t['dependency']}." if t["dependency"] else ""
        steps.append(f"- {ref}{t['title']} ({', '.join(t['labels'])}). {t['detail']}{depends}")
    report += ["", "## Process outcomes", "", *(outcomes or ["No prior experiment measured."]),
               "", f"## Next fixes for {target}", "", *steps]
    roadmap = path / "ROADMAP.md"
    old = roadmap.read_text() if roadmap.exists() else ""
    start, end = f"<!-- retro:{name} -->", f"<!-- /retro:{name} -->"
    section = "\n".join([start, f"## {target} plan from {name}", "", *steps, end])
    new = old or f"# Roadmap\n\n## Path to v1.0 (SPEC §6)\n\n{finish}\n"
    new = roadmap_section(new, start, end, section)
    gate_ids = set(REQ.findall(" ".join(ids for ids, _ in GATES)))
    evidence = [line for line in shipped if set(REQ.findall(line)) & gate_ids]
    recovery = [step for t, step in zip(tasks, steps) if t["key"].startswith("regression-")]
    if evidence or recovery:
        start, end = f"<!-- path:{name} -->", f"<!-- /path:{name} -->"
        section = "\n".join([start, f"### Path to v1.0 after {name}", "", *evidence,
                             *(["", "Recover these checks before advancing the finish-line work:", "",
                                *recovery] if recovery else []), end])
        new = roadmap_section(new, start, end, section)
    diff = "".join(unified_diff(old.splitlines(True), new.splitlines(True),
                                fromfile="a/ROADMAP.md", tofile="b/ROADMAP.md"))
    return "\n".join(report) + "\n", diff


def roadmap_section(text, start, end, section):
    if start not in text:
        return text.rstrip() + "\n\n" + section + "\n"
    left, rest = text.split(start, 1)
    if end not in rest:
        raise JobError("The roadmap retrospective section is incomplete.")
    return left + section + rest.split(end, 1)[1]


def task(key, title, detail, size="size:small", label=None, dependency=None):
    return dict(key=key, title=title, detail=detail, labels=[size, *([label] if label else [])],
                dependency=dependency)


def plan_tasks(issues, metrics, previous, shipped_ids):
    tasks, outcomes = [], []
    for item in issues:
        body = item.get("body") or ""
        if "<!-- swarm-regression:" in body and item["state"] == "open":
            tasks.append(task(f"regression-{item['number']}", f"Verify recovery from #{item['number']}",
                              f"R17. Re-run the reported failing check after #{item['number']} closes.",
                              dependency=item["number"]))
        if (item["state"] == "open" and re.search(
                r"(?i)\b(?:amend|edit|revise|change|update)\s+(?:SPEC|DECISIONS)\.md\b", item["title"])
                and not (item.get("body") or "").startswith("<!-- retro:")):
            tasks.append(task(f"decision-{item['number']}", f"Decide the contract proposal in #{item['number']}",
                              "R19 R20. Owner decision required before implementation.", label="needs-human"))
        for key, metric in enumerate(EXPERIMENTS):
            if f"<!-- retro-experiment:{key} -->" not in body:
                continue
            before, after = previous.get(metric), metrics.get(metric)
            completed = item["state"] == "closed" and item.get("state_reason") in {None, "completed"}
            result = "pending implementation or comparable evidence"
            if completed and before is not None and after is not None:
                result = f"{display(before)} → {display(after)} (expected decrease)"
                if after > before:
                    tasks.append(task(f"rollback-{item['number']}", f"Revert process change #{item['number']}",
                                      f"R19. Restore the previous workflow. {metric} worsened: {result}.",
                                      dependency=item["number"]))
            outcomes.append(f"- #{item['number']} {metric}: {result}.")
    ids, gate = next(((ids, title) for ids, title in GATES if not set(ids.split()) <= shipped_ids), GATES[0])
    tasks.append(task("finish-line-" + ids.split()[0], gate, f"{ids}. Demonstrate the SPEC §6 check with public offline evidence.",
                      "size:large"))
    for key, (metric, title) in enumerate(EXPERIMENTS.items()):
        if metrics.get(metric) and not any(f"<!-- retro-experiment:{key} -->" in (i.get("body") or "") for i in issues):
            tasks.append(task(f"experiment-{key}", title, f"R19. Expected decrease in {metric} "
                              f"from {display(metrics[metric])}.", label="process"))
    return tasks, outcomes


def checkout_preview(path, name):
    major, minor, _ = version(name)
    target = f"v{major}.{minor + 1}.0"
    metrics = dict.fromkeys(METRICS)
    tasks, outcomes = plan_tasks([], metrics, {}, set())
    report, diff = document_plan(path, name, target, metrics, {}, [], [], tasks, outcomes)
    print("Checkout-only preview. Milestone membership, next version and measurements require live role evidence.")
    print(report, diff, sep="\n")


def members(client, milestone):
    rows = pages(client, f"issues?state=all&milestone={milestone['number']}&per_page=100")
    return [i for i in rows if "pull_request" not in i]


def collect(client, milestone, all_pulls, now, issues=None, findings=()):
    if issues is None:
        issues = members(client, milestone)
    numbers = {i["number"] for i in issues}
    prs = [p for p in all_pulls if linked_issue(p) in numbers or
           (p.get("milestone") or {}).get("number") == milestone["number"]]
    commits = {p["merge_commit_sha"] for p in prs if p.get("merged_at") and p.get("merge_commit_sha")}
    issues = list(issues)
    for finding in findings:
        match = re.search(r"(?m)^Dev `([a-f0-9]{40})`$", finding.get("body") or "")
        if match and match[1] in commits and finding["number"] not in numbers:
            issues.append(finding)
    metrics, causes = measure(client, issues, prs, now)
    return issues, prs, metrics, causes


def file_issue(client, existing, marker, title, body, labels, milestone=None):
    match = next((i for i in existing if (i.get("body") or "").startswith(marker)), None)
    if match:
        return match["number"]
    for label in labels:
        client.ensure_label(label, "bfd4f2")
    data = {"title": title, "body": marker + "\n" + body, "labels": labels}
    if milestone:
        data["milestone"] = milestone
    created = client.api(f"{client.repo}/issues", method="POST", data=data)
    existing.append(created)
    return created["number"]


def due(last, complete, closed, now):
    if not last or last["complete"] != complete:
        return True
    if complete:
        return last.get("closed_issues") != closed
    return now - instant(last["ts"]) >= timedelta(days=7)


def poll(client, path, requested=None):
    now = datetime.now(timezone.utc)
    milestones = sorted((m for m in pages(client, "milestones?state=all&per_page=100")
                         if VERSION.fullmatch(m["title"])), key=lambda m: version(m["title"]))
    if requested and requested not in {m["title"] for m in milestones}:
        raise JobError("Requested milestone does not exist.")
    pulls = None
    for idx, m in enumerate(milestones):
        name = m["title"]
        last = client.journal.last(-m["number"], "retro")
        if requested and name != requested:
            continue
        if not requested and last and last.get("counts") == [m["open_issues"], m["closed_issues"]]:
            if not due(last, last["complete"], last["closed_issues"], now):
                continue
        issues = members(client, m)
        closed = sum(i["state"] == "closed" for i in issues)
        complete = bool(issues and len(issues) == closed)
        # A future milestone populated only by this planner hasn't started yet.
        started = closed or any(
            not (i.get("body") or "").startswith("<!-- retro:") or
            "in-progress" in {label["name"] for label in i.get("labels", [])}
            for i in issues
        )
        if not requested and (not started or not due(last, complete, closed, now)):
            continue
        if pulls is None:
            pulls = pages(client, "pulls?state=all&base=dev&per_page=100")
            existing = pages(client, "issues?state=all&per_page=100")
            findings = [i for i in existing if not i.get("milestone") and
                        (i.get("body") or "").startswith("<!-- swarm-regression:")]
        issues, prs, metrics, causes = collect(client, m, pulls, now, issues, findings)
        prior = {}
        if idx:
            prev = milestones[idx - 1]
            saved = client.journal.last(-prev["number"], "retro")
            prior = saved["metrics"] if saved else collect(client, prev, pulls, now, findings=findings)[2]
        shipped, ids = [], set()
        for p in prs:
            if p.get("merged_at"):
                req = set(requirements(p))
                for i in issues:
                    if i["number"] == linked_issue(p):
                        req.update(requirements(i))
                ids.update(req)
                shipped.append(f"- PR #{p['number']} {public(p['title'])} ({', '.join(sorted(req)) or 'ID missing'}).")
        tasks, outcomes = plan_tasks(issues, metrics, prior, ids)
        major, minor, _ = version(name)
        next_m = next((x for x in milestones[idx+1:] if x["state"] == "open"), None)
        target = next_m["title"] if next_m else f"v{major}.{minor + 1}.0"
        while not next_m and any(x["title"] == target for x in milestones):
            minor += 1
            target = f"v{major}.{minor + 1}.0"
        if client.dry:
            report, diff = document_plan(path, name, target, metrics, prior, shipped, causes, tasks, outcomes)
            print(report, diff, sep="\n")
            continue
        client.begin(-m["number"], client.dev())
        if not next_m:
            next_m = client.api(f"{client.repo}/milestones", method="POST", data={"title": target})
        for t in tasks:
            detail = t["detail"]
            if t["key"].startswith("experiment-"):
                detail += f"\n<!-- retro-experiment:{t['key'].split('-')[1]} -->"
            dep = f"- [ ] #{t['dependency']}" if t["dependency"] else "None"
            labels = t["labels"] if "needs-human" in t["labels"] else [*t["labels"], "agent-ready"]
            t["number"] = file_issue(client, existing, f"<!-- retro:{name}:{t['key']} -->", t["title"],
                                     detail + "\n\n### Dependencies\n\n" + dep, labels, next_m["number"])
        report, diff = document_plan(path, name, target, metrics, prior, shipped, causes, tasks, outcomes)
        filename = f"docs/retros/{name}.md"
        published = path / filename
        if diff or not published.exists() or published.read_text() != report:
            digest = sha256(report.encode()).hexdigest()
            acceptance = (f'python3 -c "from pathlib import Path; from hashlib import sha256; '
                          f"assert sha256(Path('{filename}').read_bytes()).hexdigest() == '{digest}'; "
                          f"assert '<!-- retro:{name} -->' in Path('ROADMAP.md').read_text()" + '"')
            doc = (f"R19 R20. Publish the following exact text as {filename} and apply the ROADMAP.md diff. "
                   "Preserve unrelated roadmap sections.\n\n"
                   f"~~~~markdown\n{report}~~~~\n\n~~~~diff\n{diff}~~~~\n\n"
                   f"### Acceptance test command\n\n`{acceptance}`")
            prefix = f"<!-- retro:{name}:docs-"
            pending = [i for i in existing if (i.get("body") or "").startswith(prefix) and i["state"] == "open"]
            marker = prefix + sha256(doc.encode()).hexdigest()[:16] + " -->"
            if pending:
                doc += "\n\n### Dependencies\n\n" + "\n".join(f"- [ ] #{i['number']}" for i in pending)
            file_issue(client, existing, marker, f"Publish {name} retrospective", doc, ["size:small", "agent-ready"])
        client.journal.add(-m["number"], "retro", complete=complete, closed_issues=closed,
                           counts=[m["open_issues"], m["closed_issues"]], metrics=metrics)
