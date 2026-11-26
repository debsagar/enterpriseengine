"""Point-in-time lake manifest and snapshot_at(day) cutoff filtering."""

import hashlib
import json
from pathlib import Path


def _day_key(day):
    """Normalize a business day (int or ISO date string) to a comparable string/int."""
    if isinstance(day, int):
        return f"day-{day:04d}"
    return str(day)


def _record_day(row):
    """Extract the business-effective day/timestamp string from a lake row."""
    return str(row.get("effective_at") or row.get("source_recorded_at") or "")


def filter_rows_at(rows, day):
    """Return only rows whose effective/recorded timestamp does not exceed `day`."""
    cutoff = str(day)
    kept = []
    for row in rows:
        rec_day = _record_day(row)
        if not rec_day or rec_day <= cutoff:
            kept.append(row)
    return kept


def build_manifest(day, rows_by_source, *, delta_versions=None):
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
            "latest_effective_at": max((_record_day(r) for r in filtered), default=""),
        }
    manifest = {
        "day": day,
        "day_key": _day_key(day),
        "sources": sources,
    }
    if delta_versions is not None:
        manifest["delta_versions"] = dict(delta_versions)
    return manifest


def write_manifest(root, day, rows_by_source, *, delta_versions=None):
    """Write a point-in-time manifest JSON file under `root/manifests/`."""
    manifest = build_manifest(day, rows_by_source, delta_versions=delta_versions)
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
