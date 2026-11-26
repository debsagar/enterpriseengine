"""Read-only IMAP mail reader for Norvale lake ingestion."""

import email
import imaplib
from email.utils import getaddresses, parseaddr, parsedate_to_datetime


def normalize_message(raw_bytes, *, mailbox="INBOX"):
    """Parse raw RFC822 message bytes into a lake-ready dict."""
    msg = email.message_from_bytes(raw_bytes)
    msg_id = (msg.get("Message-ID") or "").strip()
    date_hdr = (msg.get("Date") or "").strip()
    recorded_at = ""
    if date_hdr:
        try:
            recorded_at = parsedate_to_datetime(date_hdr).isoformat()
        except (TypeError, ValueError):
            recorded_at = date_hdr
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                payload = part.get_payload(decode=True)
                if payload is not None:
                    body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                    break
    else:
        payload = msg.get_payload(decode=True)
        if payload is not None:
            body = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    recipients = [addr for _, addr in getaddresses(msg.get_all("To") or []) if addr]
    return {
        "name": msg_id or f"{mailbox}:{recorded_at}:{msg.get('Subject', '')}",
        "mailbox": mailbox,
        "message_id": msg_id,
        "subject": (msg.get("Subject") or "").strip(),
        "sender": parseaddr(msg.get("From") or "")[1],
        "recipients": recipients,
        "effective_at": recorded_at,
        "source_recorded_at": recorded_at,
        "body": body.strip(),
    }


def fetch_imap_messages(host, port, user, password, *, mailbox="INBOX", limit=None):
    """Fetch raw RFC822 messages from an IMAP server (read-only)."""
    with imaplib.IMAP4(host, port, timeout=15) as client:
        client.login(user, password)
        client.select(mailbox, readonly=True)
        status, data = client.search(None, "ALL")
        if status != "OK" or not data or not data[0]:
            return []
        uids = data[0].split()
        if limit is not None:
            uids = uids[-limit:]
        messages = []
        for uid in uids:
            st, msg_data = client.fetch(uid, "(RFC822)")
            if st != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                raise RuntimeError(f"IMAP FETCH failed for uid {uid!r}: {st}")
            messages.append(normalize_message(msg_data[0][1], mailbox=mailbox))
        return messages


def scan_mail(messages, *, sink=None, source="mail", entity="Mail Message"):
    """Normalize (if raw bytes) and optionally ingest messages into `sink`."""
    docs = []
    for m in messages:
        if isinstance(m, (bytes, bytearray)):
            doc = normalize_message(m)
        else:
            doc = dict(m)
            if not doc.get("name"):
                doc["name"] = (
                    doc.get("message_id")
                    or f"{doc.get('mailbox', 'INBOX')}:{doc.get('effective_at', '')}:{doc.get('subject', '')}"
                )
        docs.append(doc)
    if sink is not None:
        return sink.ingest(source, entity, docs, effective_field="effective_at")
    return docs
