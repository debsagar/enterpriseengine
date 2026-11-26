"""Daily behaviour numbers from a run's saved role decisions. Run reporting only, never a company record."""

import json

from environments.norvale.native import judgment

CASE_TYPES = {"po-review": "purchase", "change-review": "reorder_change", "review": "finance_review"}


def empty():
    return {
        "decisions": 0,
        "case_types": {},
        "modes": {},
        "policies_proposed": 0,
        "policies_rejected": 0,
        "approvals_held": 0,
        "flagged_decisions": 0,
        "flags_overridden": 0,
        "overridden_flags": {},
    }


def count(table, name):
    table[name] = table.get(name, 0) + 1


def daily(decisions):
    """{business date: numbers}. An override is an approval of a case that carried a code-checked flag."""
    days = {}
    for key, decision in decisions.items():
        sent, out = decision["input"], decision["output"]
        day = days.setdefault(sent["as_of"], empty())
        day["decisions"] += 1
        count(day["modes"], decision.get("mode", "unknown"))
        kind = day["case_types"].setdefault(
            CASE_TYPES.get(key.split(":")[0], "other"), {"decisions": 0, "approved": 0, "actions": {}}
        )
        approved = out["action"] in judgment.APPROVALS
        kind["decisions"] += 1
        kind["approved"] += approved
        kind["approval_rate"] = round(kind["approved"] / kind["decisions"], 3)
        count(kind["actions"], out["action"])
        rejected = "policy_rejected" in out
        day["policies_proposed"] += rejected or out.get("standing_policy") is not None
        day["policies_rejected"] += rejected
        day["approvals_held"] += "held_from" in out
        # Offline and provider-outage records carry no facts; check the same evidence now.
        facts = sent.get("facts") or judgment.facts(sent["role"], sent.get("evidence", {}), sent["as_of"])
        names = {f["flag"] for f in facts["flags"]}
        day["flagged_decisions"] += bool(names)
        if approved and names:
            day["flags_overridden"] += 1
            for name in sorted(names):
                count(day["overridden_flags"], name)
    return days


def save(run, decisions, date, worked=()):
    """Rewrite <run>/behaviour.json and return the numbers for date.

    worked lists the business dates with a completed stage, so a day without decisions still has an entry.
    """
    days = daily(decisions)
    for day in worked:
        days.setdefault(day, empty())
    path = run / "behaviour.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(dict(sorted(days.items())), indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)
    return days.get(date, empty())
