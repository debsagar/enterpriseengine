"""Read-only Matrix (Synapse) room message reader for Norvale lake ingestion."""

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone


def normalize_event(event):
    """Normalize a Matrix room event dict into a lake-ready record."""
    ts_ms = event.get("origin_server_ts") or 0
    recorded_at = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat() if ts_ms else ""
    content = event.get("content") or {}
    return {
        "name": str(event.get("event_id") or ""),
        "event_id": str(event.get("event_id") or ""),
        "room_id": str(event.get("room_id") or ""),
        "sender": str(event.get("sender") or ""),
        "type": str(event.get("type") or ""),
        "body": str(content.get("body") or ""),
        "effective_at": recorded_at,
        "source_recorded_at": recorded_at,
    }


def fetch_matrix_messages(base_url, access_token, room_id, *, limit=100, timeout=15):
    """Fetch room messages from Synapse client-server API (read-only)."""
    encoded_room = urllib.parse.quote(room_id, safe="")
    url = f"{base_url.rstrip('/')}/_matrix/client/v3/rooms/{encoded_room}/messages?dir=f&limit={int(limit)}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return [normalize_event(ev) for ev in payload.get("chunk", []) if ev.get("type") == "m.room.message"]


def scan_matrix(events, *, sink=None, source="matrix", entity="Matrix Message"):
    """Normalize raw Matrix events (if needed) and optionally ingest into `sink`."""
    docs = [normalize_event(ev) if "content" in ev or "origin_server_ts" in ev else dict(ev) for ev in events]
    if sink is not None:
        return sink.ingest(source, entity, docs, effective_field="effective_at")
    return docs
