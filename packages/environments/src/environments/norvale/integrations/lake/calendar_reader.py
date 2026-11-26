"""Read-only CalDAV (Radicale) calendar reader for Norvale lake ingestion."""

import base64
import re
import urllib.request
import xml.etree.ElementTree as ET


def _unfold_ics(ics_text):
    """Unfold RFC 5545 continuation lines (CRLF/LF followed by space or tab)."""
    return re.sub(r"\r?\n[ \t]", "", ics_text)


def parse_vevents(ics_text):
    """Extract VEVENT blocks from an iCalendar (.ics) string."""
    events = []
    current = None
    for raw_line in _unfold_ics(ics_text).splitlines():
        line = raw_line.strip()
        if line == "BEGIN:VEVENT":
            current = {"attendees": []}
        elif line == "END:VEVENT" and current is not None:
            uid = current.get("uid", "")
            rec_id = current.get("recurrence_id", "")
            dtstart = current.get("dtstart", "")
            default_name = (
                f"{uid}#{rec_id}" if (uid and rec_id) else (uid or f"event:{dtstart}:{current.get('summary', '')}")
            )
            current.setdefault("name", default_name)
            current["effective_at"] = dtstart
            current["source_recorded_at"] = current.get("dtstamp") or dtstart
            events.append(current)
            current = None
        elif current is not None and ":" in line:
            key_part, value = line.split(":", 1)
            key = key_part.split(";", 1)[0].upper()
            if key == "UID":
                current["uid"] = value
            elif key == "RECURRENCE-ID":
                current["recurrence_id"] = value
            elif key == "SUMMARY":
                current["summary"] = value
            elif key == "DTSTART":
                current["dtstart"] = value
            elif key == "DTEND":
                current["dtend"] = value
            elif key == "DTSTAMP":
                current["dtstamp"] = value
            elif key == "ORGANIZER":
                current["organizer"] = value.removeprefix("mailto:")
            elif key == "ATTENDEE":
                current["attendees"].append(value.removeprefix("mailto:"))
    return events


def fetch_caldav_events(url, user, password, *, timeout=15):
    """Fetch VEVENTs from a CalDAV collection using REPORT."""
    body = (
        b'<?xml version="1.0" encoding="utf-8" ?>'
        b'<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
        b"<D:prop><D:getetag/><C:calendar-data/></D:prop>"
        b'<C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VEVENT"/></C:comp-filter></C:filter>'
        b"</C:calendar-query>"
    )
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    req = urllib.request.Request(
        url,
        data=body,
        method="REPORT",
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "application/xml; charset=utf-8",
            "Depth": "1",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        xml_bytes = resp.read()
    root = ET.fromstring(xml_bytes)
    events = []
    for elem in root.iter():
        if elem.tag.endswith("calendar-data") and elem.text:
            events.extend(parse_vevents(elem.text))
    return events


def scan_calendar(events_or_ics, *, sink=None, source="calendar", entity="Calendar Event"):
    """Parse (if iCalendar text) and optionally ingest calendar events into `sink`."""
    if isinstance(events_or_ics, str):
        docs = parse_vevents(events_or_ics)
    else:
        docs = []
        for e in events_or_ics:
            doc = dict(e)
            if not doc.get("name"):
                uid = doc.get("uid", "")
                rec_id = doc.get("recurrence_id", "")
                doc["name"] = (
                    f"{uid}#{rec_id}"
                    if (uid and rec_id)
                    else (uid or f"event:{doc.get('dtstart', '')}:{doc.get('summary', '')}")
                )
            docs.append(doc)
    if sink is not None:
        return sink.ingest(source, entity, docs, effective_field="effective_at")
    return docs
