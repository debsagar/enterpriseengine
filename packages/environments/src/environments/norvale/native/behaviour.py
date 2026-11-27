"""Daily behaviour numbers from a run's saved role decisions. Run reporting only, never a company record."""

import json
from pathlib import Path

from environments.norvale.native import judgment

CASE_TYPES = {"po-review": "purchase", "change-review": "reorder_change", "review": "finance_review"}
BUSINESS_DAYS_PER_DECISION = 5
FIRST_CLOSING_DAY = 5


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
        name = key.split(":")[1] if key.startswith("case:") else CASE_TYPES.get(key.split(":")[0], "other")
        kind = day["case_types"].setdefault(name, {"decisions": 0, "approved": 0, "actions": {}})
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


def targets(run, state, day):
    """The run-level targets of docs/BEHAVIOUR.md as pass/fail, from the saved role cases and decisions."""
    from environments.norvale.native.behaviour_roles import CONTRACTS

    cases = list(state.get("role_cases", {}).values())
    decisions = {k: v for k, v in state.get("decisions", {}).items() if k.startswith("case:")}
    elapsed = day + 1
    per_role, past_due, closed_opened = {}, [], {}
    for c in CONTRACTS:
        mine = [x for x in cases if x["contract"] == c["name"]]
        acted = [x for x in mine if x["status"] == "decided"]
        records = {x["record"] for x in mine}
        closes = {x["record"] for x in acted if x["action"] in (c.get("closes") or {x["action"]})}
        role = per_role.setdefault(c["role"], {"cases": 0, "decisions": 0, "approvals": 0})
        role["cases"] += len(mine)
        role["decisions"] += len(acted)
        role["approvals"] += sum(x.get("approval", False) for x in acted)
        closed_opened[c["owns"] + ":" + c["name"]] = {"opened": len(records), "closed": len(closes)}
        past_due += [x["key"] for x in mine if x["status"] == "open" and day > x["due"]]
    over_flag = []
    for key, d in decisions.items():
        facts = d["input"]["evidence"].get("facts", {})
        reason = d["output"].get("reason", "")
        case = state.get("role_cases", {}).get(key.removeprefix("case:"), {})
        if case.get("approval") and any(f["flag"] not in reason for f in facts.get("flags", [])):
            over_flag.append(key)
    for n in per_role.values():
        n["approval_rate"] = round(n["approvals"] / n["decisions"], 3) if n["decisions"] else None
    verification = Path(run) / "verification.json"
    checks = {
        "every_role_acts": all(
            n["decisions"] >= elapsed // BUSINESS_DAYS_PER_DECISION for n in per_role.values() if n["cases"]
        ),
        "no_silent_backlog": not past_due,
        "cases_close": elapsed < FIRST_CLOSING_DAY
        or all(v["closed"] * 2 >= v["opened"] for v in closed_opened.values()),
        "not_a_rubber_stamp": not over_flag
        and all(n["approval_rate"] != 1 for n in per_role.values() if n["decisions"] >= 10),
        "messages_grounded": not state.get("operations_round", {}).get("fixed_posts"),
        "software_unaffected": json.loads(verification.read_text()).get("ok") if verification.is_file() else None,
    }
    return dict(
        day=day,
        passed={k: v for k, v in checks.items()},
        per_role=per_role,
        past_due=past_due,
        closed_and_opened=closed_opened,
        approvals_over_unnamed_flag=over_flag,
        messages_without_case=state.get("operations_round", {}).get("fixed_posts", 0),
    )


def save(run, decisions, date, worked=(), run_targets=None):
    """Rewrite <run>/behaviour.json and return the numbers for date.

    worked lists the business dates with a completed stage, so a day without decisions still has an entry.
    """
    days = daily(decisions)
    for day in (date, *worked):  # the current date too: setup stages carry no day number
        days.setdefault(day, empty())
    if run_targets is not None:  # only runs with role contracts carry it, so older reports keep their shape
        days = {**days, "run_targets": run_targets}
    path = run / "behaviour.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(dict(sorted(days.items())), indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)
    return days[date]
