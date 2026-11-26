"""Read-only master directory reader (employees/accounts/roles) for Norvale lake."""

import json
from pathlib import Path


def normalize_account(record):
    """Normalize an employee/account record into a lake-ready master record."""
    rec_id = str(record.get("id") or record.get("name") or record.get("email") or "").strip()
    if not rec_id:
        raise ValueError(f"Master account record missing id/name/email: {record!r}")
    return {
        "name": rec_id,
        "id": rec_id,
        "full_name": str(record.get("full_name") or record.get("employee_name") or record.get("name") or ""),
        "email": str(record.get("email") or ""),
        "role": str(record.get("role") or record.get("designation") or ""),
        "department": str(record.get("department") or ""),
        "active": bool(record.get("active", True)),
        "effective_at": str(record.get("effective_at") or record.get("modified") or ""),
        "source_recorded_at": str(record.get("modified") or record.get("effective_at") or ""),
    }


def load_master_file(path):
    """Load master directory records from a JSON file (list or {'accounts': [...]})"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("accounts", data) if isinstance(data, dict) else data
    return [normalize_account(r) for r in rows]


def scan_master(records, *, sink=None, source="master", entity="Directory Account"):
    """Normalize master records and optionally ingest into `sink`."""
    docs = [normalize_account(r) for r in records]
    if sink is not None:
        return sink.ingest(source, entity, docs, effective_field="effective_at")
    return docs
