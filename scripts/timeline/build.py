"""Build the public R19/R20 project timeline from GitHub's repository API."""

import argparse
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from html import escape
import json
import os
from pathlib import Path
import re
from shutil import copyfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SHA = re.compile(r"[0-9a-f]{40}")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
PROVENANCE = re.compile(r"^<!-- swarm-provenance\n(.*?)\n-->$", re.M | re.S)
VERDICT = re.compile(r"<!-- swarm-verdict (.{1,1024}) -->")
BOUNCE = re.compile(r"<!-- swarm-bounce:[0-9a-f]{40}:[0-9a-f]{40}:[0-9a-f]{12} -->")
CONTEXTS = {"swarm": "swarm/validate", "reviewer": "review/independent"}
UPDATES = {
    "renamed": "Title changed", "ready_for_review": "Ready for review",
    "convert_to_draft": "Converted to draft", "base_ref_changed": "Base changed",
    "head_ref_force_pushed": "Head force-pushed",
}


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def record(raw):
    try:
        result = json.loads(raw)
    except ValueError:
        return {}
    return result if isinstance(result, dict) else {}


def identity(data):
    fields = []
    for key in ("model", "effort", "run"):
        value = data.get(key)
        if isinstance(value, str) and TOKEN.fullmatch(value):
            fields.append(f"{key} {value}")
    return ", ".join(fields)


def author(body):
    matches = PROVENANCE.findall(body or "")
    if len(matches) == 1 and len(matches[0]) <= 4096:
        data = record(matches[0])
        if data.get("actor") == "agent" and (details := identity(data)):
            return f"Author {details}"
    return ""


def reviewer_details(data):
    details = identity(data)
    if data.get("role") != "reviewer":
        return details
    if data.get("reviewer_fallback") is False:
        return f"{details} · Primary reviewer"
    if data.get("reviewer_fallback") is True:
        # Only publish the runner's bounded failure categories.
        reason = data.get("fallback_reason")
        if isinstance(reason, str) and reason in {
            "Claude quota or rate limit", "Claude authentication failure",
            "Could not start claude", "claude timed out",
        }:
            return f"{details} · Fallback review ({reason})"
        return f"{details} · Fallback review"
    return details


def github_api(repo, token):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo, re.ASCII):
        raise ValueError("Invalid GitHub repository name.")

    def read(path, page=None):
        suffix = f"{'&' if '?' in path else '?'}per_page=100&page={page}" if page else ""
        request = Request(f"https://api.github.com/repos/{repo}{path}{suffix}", headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        request.add_unredirected_header("Authorization", f"Bearer {token}")
        with urlopen(request, timeout=30) as response:
            return json.load(response), 'rel="next"' in response.headers.get("Link", "")

    def get(path, paginate=False):
        if not paginate:
            return read(path)[0]
        results = []
        page = 1
        while True:
            entries, more = read(path, page)
            results.extend(entries)
            if not more:
                return results
            page += 1

    return get


def collect(get):
    repo = get("")
    if repo.get("visibility") != "public":
        raise ValueError("The timeline requires a public repository.")
    owner = repo["owner"]["login"]
    project = f"https://github.com/{repo['full_name']}"
    events = []

    @lru_cache(maxsize=None)
    def statuses(head):
        try:
            return get(f"/commits/{head}/statuses", paginate=True)
        except HTTPError as error:
            if error.code != 404:
                raise
            # A force-pushed commit can disappear while its comment remains public.
            return []

    for item in get("/issues?state=all", paginate=True):
        number = int(item["number"])
        is_pr = "pull_request" in item
        pr = get(f"/pulls/{number}") if is_pr else None
        url = f"{project}/{'pull' if is_pr else 'issues'}/{number}"
        kind = "PR" if is_pr else "Issue"
        needs_human = any(label["name"] == "needs-human" for label in item["labels"])
        diff = ""
        provenance = ""
        if is_pr:
            diff = f"{pr['changed_files']} files · +{pr['additions']} / −{pr['deletions']}"
            provenance = author(pr.get("body"))

        def add(date, action, detail="", anchor="", *, needs=False):
            events.append({
                "date": stamp(date), "number": number, "title": item["title"],
                "action": action, "detail": detail, "url": url + anchor,
                "filters": ("pr" if is_pr else "issue")
                + (" merge" if action == "PR merged" else "")
                + (" needs-human" if needs or needs_human else ""),
                "diff": diff, "diff_url": url + "/files", "author": provenance,
            })

        opened = stamp(item["created_at"])
        add(item["created_at"], f"{kind} opened")
        timeline = get(f"/issues/{number}/timeline", paginate=True)
        merged = pr.get("merged_at") if is_pr else None
        if merged:
            add(merged, "PR merged")
        closed = set()
        heads = {pr["head"]["sha"]} if is_pr else set()
        verdicts = {}
        for entry in timeline:
            event = entry["event"]
            date = entry.get("created_at")
            if event == "committed" and is_pr:
                date = (entry.get("committer") or {}).get("date")
                if date and stamp(date) > opened and (not merged or stamp(date) < stamp(merged)):
                    add(date, "PR updated", f"Commit {entry['sha'][:12]}")
            elif event in {"closed", "reopened"}:
                if event == "closed":
                    closed.add(stamp(date))
                    if merged and item.get("closed_at") and stamp(date) >= stamp(item["closed_at"]):
                        continue
                add(date, f"{kind} {event}")
            elif event in {"labeled", "unlabeled"}:
                label = entry["label"]["name"]
                add(date, "Label added" if event == "labeled" else "Label removed",
                    label, needs=label == "needs-human")
            elif event in UPDATES and is_pr:
                add(date, "PR updated", UPDATES[event])
            elif event == "commented" and is_pr and (entry.get("user") or {}).get("login") == owner:
                marker = (entry.get("body") or "").split("\n", 1)[0].rstrip("\r")
                anchor = f"#issuecomment-{int(entry['id'])}"
                if BOUNCE.fullmatch(marker):
                    add(date, "PR bounced", anchor=anchor)
                elif match := VERDICT.fullmatch(marker):
                    data = record(match[1])
                    role, head, run, verdict = (data.get(key) for key in ("role", "head", "run", "verdict"))
                    if (isinstance(role, str) and role in CONTEXTS
                            and isinstance(head, str) and SHA.fullmatch(head)
                            and isinstance(run, str) and TOKEN.fullmatch(run)
                            and verdict in ("PASS", "FAIL")):
                        heads.add(head)
                        key = (head, CONTEXTS[role], run, "success" if verdict == "PASS" else "failure")
                        verdicts[key] = (date, anchor, data)

        if item.get("closed_at") and not merged and stamp(item["closed_at"]) not in closed:
            add(item["closed_at"], f"{kind} closed")

        matched = set()
        for head in sorted(heads):
            for status in statuses(head):
                context, state = status["context"], status["state"]
                if context not in CONTEXTS.values() or (status.get("creator") or {}).get("login") != owner:
                    continue
                key = (head, context, status.get("description"), state)
                evidence = verdicts.get(key)
                if evidence:
                    matched.add(key)
                    _, anchor, data = evidence
                    detail = f"{head[:12]} · {reviewer_details(data)}"
                elif head == pr["head"]["sha"]:
                    anchor = ""
                    detail = f"Head {head[:12]}"
                else:
                    continue
                add(status["created_at"], f"{context} {state}", detail, anchor)

        for key, (date, anchor, data) in verdicts.items():
            if key not in matched:
                head, context, _, _ = key
                add(date, f"{context} reported {data['verdict']}",
                    f"{head[:12]} · {reviewer_details(data)}", anchor)

    return sorted(events, key=lambda event: (event["date"], event["number"]), reverse=True)


def render(events, today):
    counts = Counter(event["action"] for event in events if event["date"].date() == today)
    summary = " · ".join(f"{count} {escape(action)}" for action, count in sorted(counts.items()))
    rows = []
    for event in events:
        date = event["date"]
        title = f"#{event['number']} {escape(event['title'])}"
        link = f'<a href="{escape(event["url"])}">{title}</a>'
        detail = f'<p>{escape(event["detail"])}</p>' if event["detail"] else ""
        diff = author_line = ""
        if event["diff"]:
            diff = f'<p><a href="{escape(event["diff_url"])}">Full diff</a> · {escape(event["diff"])}</p>'
        if event["author"]:
            author_line = f'<p>{escape(event["author"])}</p>'
        rows.append(
            f'<li data-filters="{event["filters"]}"><article>'
            f'<time datetime="{date.isoformat()}">{date:%Y-%m-%d %H:%M UTC}</time>'
            f'<p class="action">{escape(event["action"])}</p>'
            f'<h2>{link}</h2>{detail}{diff}{author_line}</article></li>'
        )
    filters = {"all": "All", "pr": "PRs", "issue": "Issues", "merge": "Merges", "needs-human": "Needs human"}
    options = "".join(f'<option value="{key}">{label}</option>' for key, label in filters.items())
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Project timeline</title>
<link rel="stylesheet" href="theme.css">
</head>
<body>
<main>
<h1>Project timeline</h1>
<section aria-labelledby="today">
<h2 id="today">What changed today</h2>
<p>{today} UTC · {summary or "No activity today"}</p>
</section>
<label for="filter">Show</label>
<select id="filter">{options}</select>
<p id="empty" role="status"{' hidden' if rows else ''}>No matching activity.</p>
<ol id="timeline">{''.join(rows)}</ol>
</main>
<script>
const filter = document.querySelector("#filter");
const rows = [...document.querySelectorAll("#timeline > li")];
function applyFilter() {{
  let matches = 0;
  for (const row of rows) {{
    row.hidden = filter.value !== "all" && !row.dataset.filters.split(" ").includes(filter.value);
    if (!row.hidden) matches++;
  }}
  document.querySelector("#empty").hidden = matches !== 0;
}}
filter.addEventListener("change", applyFilter);
applyFilter();
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline-fixture", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.offline_fixture:
            fixture = json.loads(args.offline_fixture.read_text(encoding="utf-8"))

            def get(path, paginate=False):
                return fixture["responses"][path]

            today = stamp(fixture["now"]).date()
        else:
            get = github_api(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"])
            today = datetime.now(timezone.utc).date()
        events = collect(get)
    except URLError as error:
        parser.exit(1, f"GitHub timeline fetch failed ({getattr(error, 'code', 'network error')}).\n")
    except (KeyError, ValueError):
        parser.exit(1, "Missing or invalid timeline input. Live builds require a public repository and GITHUB_TOKEN.\n")
    args.out.mkdir(parents=True, exist_ok=True)
    copyfile(Path(__file__).with_name("theme.css"), args.out / "theme.css")
    (args.out / "index.html").write_text(render(events, today), encoding="utf-8")
    print(f"Rendered {len(events)} timeline events to index.html.")


if __name__ == "__main__":
    main()
