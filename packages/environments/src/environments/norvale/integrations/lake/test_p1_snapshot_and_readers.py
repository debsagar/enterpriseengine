"""Offline tests for P1: snapshot_at(day) manifest and mail/calendar/matrix/master readers."""

import json
import tempfile
import unittest
from pathlib import Path

from environments.norvale.integrations.lake.calendar_reader import parse_vevents, scan_calendar
from environments.norvale.integrations.lake.mail_reader import normalize_message, scan_mail
from environments.norvale.integrations.lake.manifest import (
    build_manifest,
    filter_rows_at,
    snapshot_at,
    write_manifest,
)
from environments.norvale.integrations.lake.master_reader import load_master_file, scan_master
from environments.norvale.integrations.lake.matrix_reader import normalize_event, scan_matrix


class _FakeSink:
    def __init__(self):
        self.calls = []

    def ingest(self, source, entity, documents, effective_field=None):
        self.calls.append((source, entity, documents, effective_field))
        return len(documents)


class TestSnapshotManifest(unittest.TestCase):
    def test_filter_rows_at_excludes_future_records(self):
        rows = [
            {"name": "a", "effective_at": "2026-01-01T10:00:00Z"},
            {"name": "b", "effective_at": "2026-01-05T10:00:00Z"},
            {"name": "c", "effective_at": "2026-01-10T10:00:00Z"},
        ]
        kept = filter_rows_at(rows, "2026-01-05T23:59:59Z")
        self.assertEqual([r["name"] for r in kept], ["a", "b"])

    def test_write_and_load_snapshot_at(self):
        rows_by_source = {
            ("mail", "Mail Message"): [
                {"name": "m1", "effective_at": "2026-01-02T09:00:00Z"},
                {"name": "m2", "effective_at": "2026-01-08T09:00:00Z"},
            ],
            ("master", "Directory Account"): [
                {"name": "u1", "effective_at": "2026-01-01T00:00:00Z"},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = write_manifest(tmp, "2026-01-03", rows_by_source, delta_versions={"snapshots": 4})
            self.assertTrue(path.exists())
            loaded = snapshot_at(tmp, "2026-01-03")
            self.assertEqual(loaded["sources"]["mail"]["Mail Message"]["count"], 1)
            self.assertEqual(loaded["sources"]["master"]["Directory Account"]["count"], 1)
            self.assertEqual(loaded["delta_versions"], {"snapshots": 4})

            in_mem = build_manifest("2026-01-03", rows_by_source)
            self.assertEqual(
                in_mem["sources"]["mail"]["Mail Message"]["digest"],
                loaded["sources"]["mail"]["Mail Message"]["digest"],
            )


class TestReaders(unittest.TestCase):
    def test_mail_reader(self):
        raw = (
            b"From: Alice <alice@norvale.test>\r\n"
            b"To: Bob <bob@norvale.test>\r\n"
            b"Subject: Batch 42\r\n"
            b"Date: Thu, 01 Jan 2026 12:00:00 +0000\r\n"
            b"Message-ID: <msg-42@norvale.test>\r\n"
            b"\r\n"
            b"Release approved.\r\n"
        )
        doc = normalize_message(raw)
        self.assertEqual(doc["message_id"], "<msg-42@norvale.test>")
        self.assertEqual(doc["sender"], "alice@norvale.test")
        self.assertEqual(doc["recipients"], ["bob@norvale.test"])
        self.assertEqual(doc["body"], "Release approved.")
        sink = _FakeSink()
        self.assertEqual(scan_mail([raw], sink=sink), 1)
        self.assertEqual(sink.calls[0][0], "mail")

    def test_calendar_reader(self):
        ics = (
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\n"
            "UID:evt-1\n"
            "SUMMARY:Quality Review\n"
            "DTSTART:20260102T090000Z\n"
            "DTEND:20260102T100000Z\n"
            "ORGANIZER:mailto:alice@norvale.test\n"
            "ATTENDEE:mailto:bob@norvale.test\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        events = parse_vevents(ics)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["uid"], "evt-1")
        self.assertEqual(events[0]["organizer"], "alice@norvale.test")
        self.assertEqual(events[0]["attendees"], ["bob@norvale.test"])
        sink = _FakeSink()
        self.assertEqual(scan_calendar(ics, sink=sink), 1)
        self.assertEqual(sink.calls[0][0], "calendar")

    def test_matrix_reader(self):
        ev = {
            "event_id": "$ev1",
            "room_id": "!ops:norvale.test",
            "sender": "@alice:norvale.test",
            "type": "m.room.message",
            "origin_server_ts": 1767225600000,
            "content": {"msgtype": "m.text", "body": "Line 2 paused"},
        }
        doc = normalize_event(ev)
        self.assertEqual(doc["event_id"], "$ev1")
        self.assertEqual(doc["body"], "Line 2 paused")
        sink = _FakeSink()
        self.assertEqual(scan_matrix([ev], sink=sink), 1)
        self.assertEqual(sink.calls[0][0], "matrix")

    def test_master_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "accounts.json"
            p.write_text(
                json.dumps({"accounts": [{"id": "emp-1", "full_name": "Alice", "email": "alice@norvale.test", "role": "QA"}]}),
                encoding="utf-8",
            )
            docs = load_master_file(p)
            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0]["role"], "QA")
            sink = _FakeSink()
            self.assertEqual(scan_master(docs, sink=sink), 1)
            self.assertEqual(sink.calls[0][0], "master")


if __name__ == "__main__":
    unittest.main()
