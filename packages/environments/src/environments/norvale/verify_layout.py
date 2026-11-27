"""End-of-run verification and clean observable/private run directory layout."""

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


def _link_or_copy(src: Path, dst: Path):
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    os.symlink(src, dst)


def write_run_layout(runtime: Path, cfg: dict, *, revision: str, started_at: str, finished_at: str | None = None):
    """Create observable/ and private/ layout under `runtime` without breaking legacy paths."""
    run_meta = {
        "layout_version": 2,
        "scenario": cfg,
        "git_sha": revision,
        "started_at": started_at,
        "finished_at": finished_at or datetime.now(timezone.utc).isoformat(),
    }
    (runtime / "run.json").write_text(json.dumps(run_meta, indent=2) + "\n")

    observable = runtime / "observable"
    private = runtime / "private"
    observable.mkdir(parents=True, exist_ok=True)
    private.mkdir(parents=True, exist_ok=True)

    manifests_dir = runtime / "lake" / "manifests"
    if manifests_dir.is_dir():
        for mf in sorted(manifests_dir.glob("*.json")):
            day_dir = observable / "days" / mf.stem
            day_dir.mkdir(parents=True, exist_ok=True)
            _link_or_copy(mf, day_dir / "manifest.json")
    _link_or_copy(runtime / "lake" / "delta", observable / "lake")

    _link_or_copy(runtime / "business" / "trace.jsonl", private / "trace" / "trace.jsonl")
    _link_or_copy(runtime / "business" / "behaviour.json", private / "reports" / "behaviour.json")
    _link_or_copy(runtime / "provision.log", private / "logs" / "provision.log")


def verify_run(runtime: Path, cfg: dict, *, start_date: str = "2027-01-01") -> dict:
    """Verify every requested day has a manifest and no failed sources; write verification.json."""
    days = int(cfg["days"])
    base = date.fromisoformat(start_date)
    expected_days = [(base + timedelta(days=i)).isoformat() for i in range(days)]

    manifests_dir = runtime / "lake" / "manifests"
    missing_days = []
    failed_sources = {}
    found = 0
    for day in expected_days:
        mf = manifests_dir / f"{day}.json"
        if not mf.is_file():
            missing_days.append(day)
            continue
        found += 1
        data = json.loads(mf.read_text())
        failed = data.get("failed_sources") or []
        if failed:
            failed_sources[day] = failed

    ok = not missing_days and not failed_sources
    report = {
        "ok": ok,
        "requested_days": days,
        "manifests_found": found,
        "missing_days": missing_days,
        "failed_sources": failed_sources,
    }
    (runtime / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
