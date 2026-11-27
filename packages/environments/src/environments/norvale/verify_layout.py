"""The run directory layout and the end-of-run verification.

    <run>/run.json, <run>/verification.json
    <run>/observable/lake/                        the collector's archive
    <run>/observable/days/<date>/manifest.json    one per requested business day
    <run>/private/trace/trace.jsonl
    <run>/private/reports/behaviour.json
    <run>/private/logs/provision.log

Only observable/ may be handed to an extractor. Everything else in the run directory, including the
business checkpoint, credentials and release snapshot, is private.
"""

import json
import shutil
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from environments.norvale.integrations.lake.adapter import Lake
from environments.norvale.integrations.lake.flows import FLOWS
from environments.norvale.integrations.lake.manifest import RUN_START
from environments.norvale.paths import RUN_ARCHIVE

LAYOUT_VERSION = 2
PRIVATE_COPIES = {
    "business/trace.jsonl": "private/trace/trace.jsonl",
    "business/behaviour.json": "private/reports/behaviour.json",
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def _copy(source, target):
    if source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def start_run(runtime, cfg, *, git_sha, mode):
    """Create the layout and write run.json; a resumed run keeps its first start time."""
    for folder in (RUN_ARCHIVE, "observable/days", "private/trace", "private/reports", "private/logs"):
        (runtime / folder).mkdir(parents=True, exist_ok=True)
    path = runtime / "run.json"
    started = json.loads(path.read_text())["started_at"] if path.is_file() else _now()
    _save(
        path,
        dict(
            layout_version=LAYOUT_VERSION,
            run_id=runtime.name,
            mode=mode,
            scenario=cfg,
            git_sha=git_sha,
            started_at=started,
            finished_at=None,
        ),
    )
    # A report from an earlier invocation must not stand for this one.
    (runtime / "verification.json").unlink(missing_ok=True)


def business_days(cfg):
    start = date.fromisoformat(RUN_START)
    return [(start + timedelta(days=day)).isoformat() for day in range(cfg["days"])]


def end_of_day_scans(trace, days):
    """{date: problem} for days whose last end-of-day collector pass is missing or had errors."""
    last, problems = {}, {}
    if trace.is_file():
        with trace.open() as rows:
            for line in rows:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    problems["trace"] = "damaged line in the run trace"
                    continue
                if event.get("kind") == "lake_scan":
                    last[event["business_minute"]] = event["errors"]
    for number, day in enumerate(days):
        errors = last.get((number + 1) * 1440 - 1)
        if errors is None:
            problems[day] = "no end-of-day scan"
        elif errors:
            problems[day] = errors
    return problems


def configured_sources(settings):
    """The source identities the collector is commissioned to read."""
    if not settings.is_file():
        return set()
    entries = json.loads(settings.read_text()).values()
    return {entry.get("source") or entry["reader"]["source_id"] for entry in entries}


def verify_run(runtime, cfg, *, completed):
    """Check the delivered run and write verification.json."""
    days = business_days(cfg)
    expected = configured_sources(runtime / "analytics.json")
    missing_days, failed_sources, unreported = [], {}, {}
    for day in days:
        path = runtime / "observable/days" / day / "manifest.json"
        if not path.is_file():
            missing_days.append(day)
            continue
        manifest = json.loads(path.read_text())
        failed = manifest.get("failed_sources", [])
        if failed:
            failed_sources[day] = failed
        if silent := sorted(expected - set(failed) - set(manifest.get("read_sources", []))):
            unreported[day] = silent
    archive = runtime / RUN_ARCHIVE
    rows = Lake(archive / "delta", {}).index()
    undated = Counter(f"{row['source']}/{row['entity']}" for row in rows if not row.get("business_date"))
    present = {row["entity"] for row in rows}
    state = archive / "state.json"
    entries = json.loads(state.read_text()).values() if state.is_file() else ()
    read = {entry["entity"] for entry in entries if entry.get("last_success")}
    missing = {
        flow: gaps
        for flow, chain in FLOWS.items()
        if (
            gaps := {
                entity: "no records" if entity in read else "never read" for entity in chain if entity not in present
            }
        )
    }
    scans = end_of_day_scans(runtime / "private/trace/trace.jsonl", days)
    report = dict(
        ok=completed and not (missing_days or failed_sources or unreported or undated or missing or scans),
        run_completed=completed,
        requested_days=len(days),
        manifests_found=len(days) - len(missing_days),
        missing_days=missing_days,
        failed_sources=failed_sources,
        unreported_sources=unreported,
        end_of_day_scan_problems=scans,
        rows=len(rows),
        undated_rows=dict(sorted(undated.items())),
        missing_flow_entities=missing,
    )
    _save(runtime / "verification.json", report)
    return report


def finish_run(runtime, cfg, *, completed):
    """Put the day manifests and private records in place, verify them and close run.json."""
    for day in business_days(cfg):
        _copy(runtime / RUN_ARCHIVE / "manifests" / f"{day}.json", runtime / "observable/days" / day / "manifest.json")
    for source, target in PRIVATE_COPIES.items():
        _copy(runtime / source, runtime / target)
    report = verify_run(runtime, cfg, completed=completed)
    path = runtime / "run.json"
    _save(path, dict(json.loads(path.read_text()), finished_at=_now()))
    return report
