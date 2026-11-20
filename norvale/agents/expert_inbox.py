"""Owner-hosted, opt-in interview exercise on the existing company mail service."""
import hashlib
import hmac
import json
import secrets
import re
from datetime import date, timedelta
from pathlib import Path
from email.utils import parsedate_to_datetime

VERSION = 1
EXPERTS = {"MAINT_LEAD": "equipment availability", "QA_MGR": "quality review availability"}


def briefing(seed, case, role, opened):
    """Private to the named expert and the owner. Change VERSION for new semantics."""
    key = f"expert-inbox/{VERSION}/{case}/{opened}/{role}".encode()
    offset = 1 + hmac.digest(seed, key, "sha256")[0] % 7
    return (opened + timedelta(days=offset)).isoformat()


class ExpertInbox:
    """Give the agent only question(), ask(expert), and inbox() as bound tools.

    The controller owns the role-bound mail clients, private seed, clock and grader.
    Each case represents one planning exercise for one planner mailbox.
    """
    def __init__(self, case, opened, seed, clients, now, directory):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", case) or len(seed) < 16:
            raise ValueError("Provide a case ID and at least 16 private seed bytes")
        self.directory = Path(directory)
        self.case, self.opened, self.seed = case, opened, seed
        self.clients, self.now = clients, now
        self.prefix = f"[expert-inbox/v{VERSION}/{case}/{opened}]"

    def question(self):
        return {"version": VERSION, "case": self.case, "opened": self.opened.isoformat(),
                "question": "What is the earliest date on which both equipment and quality review "
                            "are available? Cite the two expert replies, or report unknown.",
                "contacts": dict(EXPERTS)}

    def _mail(self, sender, recipient, subject, body, send=False, in_reply_to=None):
        client = self.clients[recipient]
        def lookup():
            rows = [m for m in client.find_subject(subject) if m["subject"] == subject
                    and m["from"] == self.clients[sender].addr and m["to"] == client.addr]
            if len(rows) > 1:
                raise ValueError("Ambiguous mail evidence")
            if not rows:
                return None
            msg = client.read_mail(rows[0]["uid"])
            if msg["body"].rstrip("\r\n") != body or not msg["message_id"]:
                raise ValueError("Changed mail evidence")
            if parsedate_to_datetime(msg["date"]) > self.now():
                if send:
                    raise ValueError("Message is outside the business cutoff")
                return None
            return msg
        found = lookup()
        if send and found is None:
            # Claim before SMTP. After an uncertain send only native readback can confirm delivery.
            claim = self.directory / hashlib.sha256(subject.encode()).hexdigest()
            try:
                claim.touch(exist_ok=False)
            except FileExistsError as exc:
                raise RuntimeError("Pending delivery; check the existing send before resending") from exc
            self.clients[sender].send_mail([client.addr], subject, body, self.now(), in_reply_to=in_reply_to)
            found = lookup()
            if found is None:
                raise RuntimeError("Delivery pending; reconcile mailbox before retry")
        return found

    def _request(self, expert, send=False):
        topic = EXPERTS[expert]
        if self.now().date() < self.opened:
            raise ValueError("Case has not opened")
        return self._mail("PLANNER", expert, f"{self.prefix} request {expert}",
                          f"What is the earliest available date for {topic} on case {self.case}?", send)

    def ask(self, expert):
        return self._request(expert, send=True)["message_id"]

    def _reply(self, expert, send=False):
        request = self._request(expert)
        if request is None:
            return None
        day = briefing(self.seed, self.case, expert, self.opened)
        thread = hashlib.sha256(request["message_id"].encode()).hexdigest()[:20]
        return self._mail(expert, "PLANNER", f"{self.prefix} reply {expert} {thread}",
                          f"{EXPERTS[expert]} for {self.case}: {day}.", send,
                          in_reply_to=request["message_id"])

    def respond(self, expert):
        """Called by the host for this expert only, at its scheduled business time."""
        msg = self._reply(expert, send=True)
        return msg["message_id"] if msg else None

    def inbox(self):
        return [{"expert": expert, "message_id": m["message_id"], "body": m["body"]}
                for expert in EXPERTS if (m := self._reply(expert))]

    def grade(self, answer, citations):
        """Only the owner calls this. Unavailable evidence warrants an unknown answer."""
        replies = self.inbox()
        if len(replies) < len(EXPERTS):
            return answer == "unknown" and set(citations) <= {m["message_id"] for m in replies}
        expected = max(briefing(self.seed, self.case, role, self.opened) for role in EXPERTS)
        return answer == expected and set(citations) == {m["message_id"] for m in replies}


def open_case(run, case, *, create=False):
    """Open a case against the existing run's mail accounts and shared business clock."""
    from norvale.agents.comms import Comms
    from norvale.environment.clock import Clock
    from walkthrough.engine import ROLES

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", case):
        raise ValueError("Invalid case ID")
    env = Path(run) / "env"
    clock = Clock(env)
    endpoints = json.loads((env / "endpoints.json").read_text())
    accounts = json.loads((env / "secrets.json").read_text())
    clients = {r["role_id"]: Comms({"mail": r["email"].replace(".example", ".test"),
                                    "chat": r["role_id"].lower()}, accounts[r["role_id"]], endpoints)
               for r in ROLES if r["role_id"] in {*EXPERTS, "PLANNER"}}
    directory = Path(run) / "private" / "expert-inbox-v1" / case
    state = directory / "scenario.json"
    if create:
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        with state.open("x") as stream:
            json.dump({"version": VERSION, "opened": clock.now().date().isoformat(),
                       "seed": secrets.token_hex(32)}, stream)
    saved = json.loads(state.read_text())
    if saved["version"] != VERSION:
        raise ValueError("Unsupported interview version")
    return ExpertInbox(case, date.fromisoformat(saved["opened"]), bytes.fromhex(saved["seed"]),
                       clients, clock.now, directory)


def answer_pending(run):
    for state in sorted((Path(run) / "private" / "expert-inbox-v1").glob("*/scenario.json")):
        case = open_case(run, state.parent.name)
        for expert in EXPERTS:
            case.respond(expert)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--case", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "question", "inbox"):
        sub.add_parser(name)
    sub.add_parser("ask").add_argument("expert", choices=EXPERTS)
    grade = sub.add_parser("grade")
    grade.add_argument("answer")
    grade.add_argument("citation", nargs="*")
    args = parser.parse_args()
    interview = open_case(args.run, args.case, create=args.command == "init")
    if args.command == "ask":
        result = interview.ask(args.expert)
    elif args.command == "grade":
        result = interview.grade(args.answer, args.citation)
    elif args.command == "inbox":
        result = interview.inbox()
    else:
        result = interview.question()
    print(json.dumps(result, indent=2))
