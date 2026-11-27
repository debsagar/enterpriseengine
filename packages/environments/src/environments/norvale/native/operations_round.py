"""The code-written daily operations round: chat status posts and the next day's review invite.

No model is involved. Every record name in a post is read from ERPNext through the posting role.
"""

import hashlib
import json

from environments.norvale.agents.comms import Comms
from environments.norvale.native import behaviour_roles
from environments.norvale.walkthrough import ROLES

CHAIR = "PLANNER"
# Chat identity, ERP role that reads, ERPNext list filters and the heading of its post.
POSTS = (
    (
        "PLANNER",
        "PLANNER",
        "Work Order",
        {"docstatus": 1, "status": ["not in", ["Completed", "Stopped"]]},
        "Open work orders",
    ),
    (
        "SALES_OPS_MGR",
        "SALES",
        "Sales Order",
        {"docstatus": 1, "per_delivered": ["<", 100]},
        "Sales orders to deliver",
    ),
    (
        "BUYER_CHEM",
        "BUYER",
        "Purchase Order",
        {"docstatus": ["<", 2], "per_received": ["<", 100]},
        "Purchases awaiting receipt",
    ),
)


def case_messages(engine, done, client):
    """One chat message per decided role case, from the deciding role, naming the record. Never without a case."""
    for case in engine.state.get("role_cases", {}).values():
        if case["status"] == "open" or case.get("posted"):
            continue
        sender = client(case["role"])
        sender.join(done["room"])
        text = engine.compose(
            "chat:" + case["key"], case["role"], "Daily operations", "Case " + case["record"], case["message"]
        )
        sender.send_chat(
            done["room"], text, transaction="case-" + hashlib.sha256(case["key"].encode()).hexdigest()[:32]
        )
        case["posted"] = engine.day
        engine.persist()


def run(engine):
    """Post today's status and invite the posters to tomorrow's review. Safe to repeat for a day."""
    done = engine.state.setdefault("operations_round", {"room": None, "days": []})
    today = engine.date()
    if today in done["days"]:
        return
    secrets = json.loads((engine.run / "env/secrets.json").read_text())
    emails = {row["role_id"]: row["email"].replace(".example", ".test") for row in ROLES}

    def client(identity):
        return Comms({"mail": emails[identity], "chat": identity.lower()}, secrets[identity], engine.eps)

    chair = client(CHAIR)
    roles_on = (getattr(engine, "config", None) or {}).get("behaviour_roles")
    invited = [identity for identity, *_ in POSTS if identity != CHAIR]
    if roles_on:  # every role that can post a case message must be in the invite-only room
        invited += sorted({c["role"] for c in behaviour_roles.CONTRACTS} - {CHAIR, *invited})
    if not done["room"]:
        done["room"] = chair.create_room("Daily operations", [identity.lower() for identity in invited])
        engine.persist()
    # Joining is repeatable, so a resumed day completes the membership of the saved room.
    for identity in invited:
        client(identity).join(done["room"])
    if roles_on:
        case_messages(engine, done, client)
    else:
        for identity, role, doctype, filters, heading in POSTS:
            names = [row["name"] for row in engine.observe(role, doctype, filters, ["name"])]
            listed = ", ".join(sorted(names)) or "none"
            send = f"{today} {heading}: {listed}"
            client(identity).send_chat(done["room"], send, transaction=f"ops-{today}-{identity}")
            done["fixed_posts"] = done.get("fixed_posts", 0) + 1
    tomorrow = engine.dated(engine.day + 1).replace("-", "")
    uid = f"operations-review-{tomorrow}@norvale.test"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Norvale//Operations//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{today.replace('-', '')}T120000Z",
        f"DTSTART:{tomorrow}T083000Z",
        f"DTEND:{tomorrow}T090000Z",
        "SUMMARY:Daily operations review",
        f"ORGANIZER:mailto:{emails[CHAIR]}",
        *(f"ATTENDEE:mailto:{emails[identity]}" for identity, *_ in POSTS),
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ]
    # Calendars are private to their owner, so each attendee holds its own copy of the invitation.
    for identity, *_ in POSTS:
        attendee = client(identity)
        attendee.ensure_calendar()
        attendee.put_event(uid.split("@")[0], "\r\n".join(lines))
    done["days"].append(today)
    engine.persist()
    engine.log("operations_round", date=today, room=done["room"], event=uid)
