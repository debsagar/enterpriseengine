"""The code-written daily operations round: chat status posts and the next day's review invite.

No model is involved. Every record name in a post is read from ERPNext through the posting role.
"""

import json

from environments.norvale.agents.comms import Comms
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
    if not done["room"]:
        invited = [identity for identity, *_ in POSTS if identity != CHAIR]
        room = chair.create_room("Daily operations", [identity.lower() for identity in invited])
        for identity in invited:
            client(identity).join(room)
        done["room"] = room
        engine.persist()
    for identity, role, doctype, filters, heading in POSTS:
        names = [row["name"] for row in engine.observe(role, doctype, filters, ["name"])]
        listed = ", ".join(sorted(names)) or "none"
        client(identity).send_chat(done["room"], f"{today} {heading}: {listed}", transaction=f"ops-{today}-{identity}")
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
    chair.ensure_calendar()
    chair.put_event(uid.split("@")[0], "\r\n".join(lines))
    done["days"].append(today)
    engine.persist()
    engine.log("operations_round", date=today, room=done["room"], event=uid)
