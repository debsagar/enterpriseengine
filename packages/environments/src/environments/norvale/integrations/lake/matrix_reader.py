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
    ev_id = str(event.get("event_id") or "")
    return {
        "name": ev_id or f"{event.get('room_id', '')}:{ts_ms}:{event.get('sender', '')}",
        "event_id": ev_id,
        "room_id": str(event.get("room_id") or ""),
        "sender": str(event.get("sender") or ""),
        "type": str(event.get("type") or ""),
        "body": str(content.get("body") or ""),
        "effective_at": recorded_at,
        "source_recorded_at": recorded_at,
    }


def joined_matrix_rooms(base_url, access_token, *, timeout=15):
    """Room IDs this identity has joined."""
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/_matrix/client/v3/joined_rooms",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))["joined_rooms"]


def fetch_matrix_messages(base_url, access_token, room_id, *, limit=100, timeout=15, since=None, cursor=None):
    """Fetch room messages from Synapse client-server API (read-only, paginated)."""
    encoded_room = urllib.parse.quote(room_id, safe="")
    messages = []
    token = cursor.get("batch") if cursor is not None else since
    remaining = int(limit) if limit is not None else None
    while True:
        page_limit = 100 if remaining is None else min(100, remaining)
        if page_limit <= 0:
            break
        qs = f"dir=f&limit={page_limit}"
        if token:
            qs += f"&from={urllib.parse.quote(str(token), safe='')}"
        url = f"{base_url.rstrip('/')}/_matrix/client/v3/rooms/{encoded_room}/messages?{qs}"
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        chunk = payload.get("chunk", [])
        for ev in chunk:
            if ev.get("type") == "m.room.message":
                messages.append(normalize_event(ev))
        end_token = payload.get("end")
        if cursor is not None and end_token:
            cursor["batch"] = end_token
        if not end_token or end_token == token:
            break
        token = end_token
        if remaining is not None:
            remaining -= len(chunk)
    return messages


def scan_matrix(events, *, sink=None, source="matrix", entity="Matrix Message"):
    """Normalize raw Matrix events (if needed) and optionally ingest into `sink`."""
    docs = []
    for ev in events:
        if "content" in ev or "origin_server_ts" in ev:
            doc = normalize_event(ev)
        else:
            doc = dict(ev)
            if not doc.get("name"):
                doc["name"] = (
                    doc.get("event_id")
                    or f"{doc.get('room_id', '')}:{doc.get('effective_at', '')}:{doc.get('sender', '')}"
                )
        docs.append(doc)
    if sink is not None:
        return sink.ingest(source, entity, docs, effective_field="effective_at")
    return docs
