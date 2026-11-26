"""Point-in-time lake manifest, snapshot_at(day) cutoff filtering, and fork staging."""

import hashlib
import json
import re
from datetime import datetime, time, timezone
from pathlib import Path

_COMPACT_DT = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)$")
_COMPACT_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _day_key(day):
    """Normalize a business day (int or ISO date string) to a filename-safe key."""
    if isinstance(day, int) and not isinstance(day, bool):
        return f"day-{day:04d}"
    return str(day)


def _parse_ts(value, *, end_of_day=False):
    """Parse an int day, ISO date/datetime, or compact iCal date/datetime into a comparable tuple."""
    if value is None or value == "":
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return ("int", value)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit() and len(text) < 8:
        return ("int", int(text))
    if m := _ISO_DATE.match(text):
        d = datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
        if end_of_day:
            d = datetime.combine(d.date(), time.max, tzinfo=timezone.utc)
        return ("dt", d)
    if m := _COMPACT_DATE.match(text):
        d = datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
        if end_of_day:
            d = datetime.combine(d.date(), time.max, tzinfo=timezone.utc)
        return ("dt", d)
    if m := _COMPACT_DT.match(text):
        d = datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), int(m[6]), tzinfo=timezone.utc)
        return ("dt", d)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return ("dt", dt)
    except ValueError:
        return ("str", text)


def _record_day(row):
    """Extract the business-effective day/timestamp from a lake row."""
    eff = row.get("effective_at")
    if eff is not None and eff != "":
        return eff
    rec = row.get("source_recorded_at")
    if rec is not None and rec != "":
        return rec
    return None


def is_dated(row):
    """Whether the row carries a date a calendar cutoff can compare."""
    stamp = _parse_ts(_record_day(row))
    return stamp is not None and stamp[0] == "dt"


def filter_rows_at(rows, day):
    """Return only rows whose effective/recorded timestamp does not exceed `day`.

    Rows without a valid timestamp are excluded so missing timestamps never bypass cutoffs.
    """
    cutoff = _parse_ts(day, end_of_day=True)
    if cutoff is None:
        raise ValueError(f"Invalid cutoff day: {day!r}")
    kept = []
    for row in rows:
        rec_raw = _record_day(row)
        rec_ts = _parse_ts(rec_raw, end_of_day=False)
        if rec_ts is None:
            continue
        if rec_ts[0] != cutoff[0]:
            raise ValueError(f"Incompatible timestamp {rec_raw!r} for cutoff {day!r}")
        if rec_ts[1] <= cutoff[1]:
            kept.append(row)
    return kept


def build_manifest(day, rows_by_source, *, delta_versions=None, failed_sources=(), undated=()):
    """Build a point-in-time manifest dict for `day` from `(source, entity) -> rows`."""
    sources = {}
    for (source, entity), rows in sorted(rows_by_source.items()):
        filtered = filter_rows_at(rows, day)
        digests = sorted(
            str(r.get("content_hash") or hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest())
            for r in filtered
        )
        summary_hash = hashlib.sha256("\n".join(digests).encode()).hexdigest()
        sources.setdefault(source, {})[entity] = {
            "count": len(filtered),
            "digest": summary_hash,
            "latest_effective_at": max((str(_record_day(r)) for r in filtered), default=""),
        }
    manifest = {
        "day": day,
        "day_key": _day_key(day),
        "sources": sources,
    }
    if delta_versions is not None:
        manifest["delta_versions"] = dict(delta_versions)
    if failed_sources:
        manifest["failed_sources"] = sorted(failed_sources)
    if undated:
        manifest["undated"] = sorted(undated)
    return manifest


def write_manifest(root, day, rows_by_source, **options):
    """Write a point-in-time manifest JSON file under `root/manifests/`."""
    manifest = build_manifest(day, rows_by_source, **options)
    out_dir = Path(root) / "manifests"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{manifest['day_key']}.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def snapshot_at(root, day, *, rows_by_source=None):
    """Load the manifest for `day` if present, or build it in-memory from `rows_by_source`."""
    path = Path(root) / "manifests" / f"{_day_key(day)}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if rows_by_source is not None:
        return build_manifest(day, rows_by_source)
    raise FileNotFoundError(f"No manifest for {day!r} at {path}")


def stage_snapshot(staging_dir, day, rows_by_source, *, delta_versions=None):
    """Write an isolated, cutoff-filtered lake snapshot + manifest into `staging_dir` for Engine.fork()."""
    lake_dir = Path(staging_dir) / "lake"
    lake_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = write_manifest(lake_dir, day, rows_by_source, delta_versions=delta_versions)
    records_dir = lake_dir / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    for (source, entity), rows in sorted(rows_by_source.items()):
        filtered = filter_rows_at(rows, day)
        safe_name = f"{source}__{entity}".replace("/", "_").replace(" ", "_")
        (records_dir / f"{safe_name}.json").write_text(
            json.dumps(filtered, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return manifest_path
