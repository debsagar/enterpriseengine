"""Single-writer Delta archive of records already read through source permissions."""

import hashlib
import json
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
from deltalake import DeltaTable, QueryBuilder, write_deltalake

from .manifest import business_date

# (business date this pass observes, whether the source stamps records on the simulated clock).
# Set by the company collector; standalone scans leave business_date empty.
observation = ContextVar("lake_observation", default=None)


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Lake:
    def __init__(self, path, allowed):
        self.path = str(Path(path).resolve())
        self.allowed = {role: set(entities) for role, entities in allowed.items()}

    def latest_hashes(self, source, entity):
        latest = {}
        if Path(self.path, "_delta_log").exists():
            # The payload history can be large. Deduplication needs only the most
            # recent digest per native record in the source/entity being scanned.
            source_literal = source.replace("'", "''")
            entity_literal = entity.replace("'", "''")
            query = (
                "SELECT native_id, first_value(content_hash ORDER BY ingested_at DESC) AS digest "
                f"FROM snapshots WHERE source = '{source_literal}' AND entity = '{entity_literal}' "
                "GROUP BY native_id"
            )
            reader = QueryBuilder().register("snapshots", DeltaTable(self.path)).execute(query)
            latest = {row["native_id"]: row["digest"] for row in pa.table(reader.read_all()).to_pylist()}
        return latest

    def _columns(self):
        return {field.name for field in DeltaTable(self.path).schema().fields}

    def latest_dates(self, source, entity):
        """The business date of each record's newest snapshot."""
        if not Path(self.path, "_delta_log").exists() or "business_date" not in self._columns():
            return {}
        source_literal = source.replace("'", "''")
        entity_literal = entity.replace("'", "''")
        query = (
            "SELECT native_id, max(business_date) AS day "
            f"FROM snapshots WHERE source = '{source_literal}' AND entity = '{entity_literal}' "
            "GROUP BY native_id"
        )
        reader = QueryBuilder().register("snapshots", DeltaTable(self.path)).execute(query)
        return {row["native_id"]: row["day"] for row in pa.table(reader.read_all()).to_pylist()}

    def ingest(self, source, entity, documents, effective_field=None, *, latest=None):
        """Append confirmed source readbacks. Caller must serialize writers and filter secrets."""
        if latest is None:
            latest = self.latest_hashes(source, entity)
        seen = observation.get()
        dates = None
        pending = {}
        new = []
        for doc in documents:
            payload = canonical(doc)
            digest = hashlib.sha256(payload.encode()).hexdigest()
            key = str(doc["name"])
            if pending.get(key, latest.get(key)) == digest:
                continue
            pending[key] = digest
            day = ""
            if seen:
                on, simulated = seen
                stamps = [doc.get("modified"), doc.get("creation")] if simulated else []
                if effective_field:
                    stamps.append(doc.get(effective_field))
                if dates is None:
                    dates = self.latest_dates(source, entity)
                day = dates[key] = business_date(stamps, on, dates.get(key))
            new.append(
                dict(
                    source=source,
                    entity=entity,
                    native_id=key,
                    content_hash=digest,
                    source_recorded_at=str(doc.get("modified") or ""),
                    effective_at=str(doc.get(effective_field) or "") if effective_field else "",
                    ingested_at=now(),
                    business_date=day,
                    payload=payload,
                )
            )
        if new:
            write_deltalake(self.path, pa.Table.from_pylist(new), mode="append", schema_mode="merge")
            latest.update(pending)
        return len(new)

    def _rows(self, version=None, *, entity=None):
        if not Path(self.path, "_delta_log").exists():
            return []
        table = DeltaTable(self.path, version=version)
        query = "SELECT * FROM snapshots"
        if entity is not None:
            literal = entity.replace("'", "''")
            query += f" WHERE entity = '{literal}'"
        batches = QueryBuilder().register("snapshots", table).execute(query)
        return pa.table(batches.read_all()).to_pylist()

    def index(self):
        """Every snapshot's identity, digest and dates, without payloads."""
        if not Path(self.path, "_delta_log").exists():
            return []
        columns = "source, entity, native_id, content_hash, source_recorded_at, effective_at, ingested_at"
        if "business_date" in self._columns():
            columns += ", business_date"
        query = f"SELECT {columns} FROM snapshots"
        batches = QueryBuilder().register("snapshots", DeltaTable(self.path)).execute(query)
        return pa.table(batches.read_all()).to_pylist()

    def read(self, role, entity, version=None):
        if entity not in self.allowed.get(role, set()):
            raise PermissionError(f"{role} cannot read {entity}")
        return self._rows(version, entity=entity)

    def history(self):
        return DeltaTable(self.path).history() if Path(self.path, "_delta_log").exists() else []
