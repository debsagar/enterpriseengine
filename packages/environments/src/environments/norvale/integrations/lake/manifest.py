"""Point-in-time lake manifest, snapshot_at(day) cutoff filtering, and fork staging."""

import hashlib
import json
import re
from datetime import datetime, time, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

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


RUN_START = "2027-01-01"


def business_date(stamps, observed, previous=None):
    """The business date from which a snapshot is part of the company's state.

    The first of the record's own `stamps` that falls between the run start and the `observed` date and,
    for a later version, after the previous version's date. Otherwise the observed date. This rejects
    wall-clock stamps, future effective dates, and a posting date that an update left unchanged.
    """
    for stamp in stamps:
        day = _calendar_day(stamp)
        if day and RUN_START <= day <= observed and (previous is None or day > previous):
            return day
    return observed


def _calendar_day(stamp):
    """The UTC date of a timestamp, or None when it has no comparable date."""
    try:
        parsed = _parse_ts(stamp)
    except ValueError:
        return None
    if parsed is None or parsed[0] != "dt":
        return None
    return parsed[1].astimezone(timezone.utc).date().isoformat()


def row_day(row):
    """The date from which a snapshot counts in a daily manifest, or None."""
    return _calendar_day(_record_day(row))


def _record_day(row):
    """Extract the business day/timestamp from a lake row."""
    for field in ("business_date", "effective_at", "source_recorded_at"):
        value = row.get(field)
        if value is not None and value != "":
            return value
    return None


def is_dated(row):
    """Whether the row carries a date a calendar cutoff can compare."""
    return row_day(row) is not None


def _content_digest(row):
    return str(row.get("content_hash") or hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest())


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


def state_at(rows, day):
    """Each record's last archived version dated by `day`. Rows without a native ID are all kept."""
    state = {}
    ordered = sorted(filter_rows_at(rows, day), key=lambda r: r.get("ingested_at") or "")
    for number, row in enumerate(ordered):
        state[("id", row["native_id"]) if "native_id" in row else ("row", number)] = row
    return list(state.values())


def build_manifest(day, rows_by_source, *, delta_versions=None, failed_sources=(), undated=(), read_sources=()):
    """Build a point-in-time manifest dict for `day` from `(source, entity) -> rows`."""
    sources = {}
    for (source, entity), rows in sorted(rows_by_source.items()):
        filtered = state_at(rows, day)
        digests = sorted(map(_content_digest, filtered))
        summary_hash = hashlib.sha256("\n".join(digests).encode()).hexdigest()
        sources.setdefault(source, {})[entity] = {
            "count": len(filtered),
            "digest": summary_hash,
            "latest_effective_at": max((str(_record_day(r)) for r in filtered), default=""),
        }
        # Names the exact version of every record, so a snapshot resolves to payloads in the archive.
        # ponytail: grows with records x days; move to a per-day side table if manifests get large.
        if versions := {r["native_id"]: _content_digest(r) for r in filtered if "native_id" in r}:
            sources[source][entity]["records"] = versions
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
    if read_sources:
        # Includes sources that were read and hold nothing dated by this day.
        manifest["read_sources"] = sorted(read_sources)
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
    staging_dir = Path(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    lake_dir = staging_dir / "lake"
    with TemporaryDirectory(dir=staging_dir) as tmp:
        prepared = Path(tmp) / "lake"
        manifest_path = write_manifest(prepared, day, rows_by_source, delta_versions=delta_versions)
        records_dir = prepared / "records"
        records_dir.mkdir()
        for (source, entity), rows in sorted(rows_by_source.items()):
            filtered = state_at(rows, day)
            safe_name = f"{source}__{entity}".replace("/", "_").replace(" ", "_")
            (records_dir / f"{safe_name}.json").write_text(
                json.dumps(filtered, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        backup = Path(tmp) / "previous"
        if lake_dir.exists():
            lake_dir.rename(backup)
        try:
            prepared.rename(lake_dir)
        except OSError:
            if backup.exists():
                backup.rename(lake_dir)
            raise
    return lake_dir / manifest_path.relative_to(prepared)
