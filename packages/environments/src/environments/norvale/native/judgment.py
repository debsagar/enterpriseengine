"""What a role's decision prompt says, how a model is asked, and which answers may execute."""

import json
import os
import urllib.request

from environments.norvale.native import long_run
from environments.norvale.walkthrough import ROLES

ACTIONS = ("approve", "approve_with_condition", "request_info", "reject")
APPROVALS = ("approve", "approve_with_condition")
MANDATES = {
    "SCM_MGR": "Approve or reject supplier purchases and item reorder changes using observed evidence; issue scoped purchasing policies.",
    "CFO": "Review finances and revise purchasing policies; no configured spending ceiling.",
    "BUYER": "Observe materials, request purchases; cannot approve own purchase or book stock.",
    "IT_ADMIN": "Implement separately approved item changes after approval date.",
    "PRODUCTION": "Execute authorized operations within available stock and shift capacity.",
}

INSTRUCTION = (
    "Return JSON with action, reason, message, cites. action is one of: approve; approve_with_condition (approval "
    "takes effect now and condition, a string naming one concrete follow-up, is assigned as a task); request_info "
    "(nothing is approved; message asks for the missing fact); reject (the request is closed). cites is a list of "
    "paths into this input that the reason rests on, such as evidence.observations.grand_total or "
    "facts.flags.duplicate_open_order; an approval without a resolvable path is not executed. Do not assume unseen "
    "facts. Prior decisions may be mistaken: reassess using current native evidence and explicit operating "
    "policies; do not perpetuate unsupported concerns. facts holds values computed by code from the evidence; "
    "facts.flags are contradictions found by code. Do not approve over a flag unless the reason names it and cites "
    "the evidence that resolves it. role_card states your duty, authority limits and escalation; policy holds the "
    "effective standing policies. Approval is not implementation. You may propose standing_policy with "
    "effective_date, review_date, nonnegative numeric approval_limit and reorder_target, nonempty exact suppliers "
    "and items lists. Only SCM_MGR/CFO may authorize it. When the same kind of purchase will recur and the "
    "evidence supports a rule, propose one so routine cases need no further judgment. Ground all values in "
    "observed evidence; omit if insufficient."
)


def roster(role):
    rows = {r["role_id"]: r for r in ROLES}
    return rows.get({"SCM_MGR": "SC_MGR", "BUYER": "BUYER_CHEM"}.get(role, role), {})


def limits(role):
    """The written authority limits for an ERP role, from the company roster."""
    return roster(role).get("write_authority", {}).get("limits", {})


def role_card(role):
    row = roster(role)
    escalation = row.get("escalation_edge", {})
    return {
        "role": role,
        "title": row.get("title"),
        "duty": MANDATES.get(role),
        "goal": row.get("mandate"),
        "authority_limits": limits(role),
        "escalates_to": escalation.get("escalates_to"),
        "escalation_triggers": escalation.get("triggers", []),
    }


def resolves(node, path):
    """Whether a cited path names something present: dict keys, list indexes, flag or item names."""
    for part in [p for p in str(path).replace("[", ".").replace("]", "").split(".") if p]:
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list):
            named = [r for r in node if isinstance(r, dict) and part in (r.get("flag"), r.get("item_code"))]
            if part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            elif named:
                node = named[0]
            else:
                return False
        else:
            return False
    return True


def graded(sent, answer):
    """Hold an approval the role may not execute. Nothing is ever upgraded to an approval."""
    if answer.get("action") in ("defer", "investigate"):  # earlier wording for "not approved yet"
        answer = {**answer, "action": "request_info"}
    if answer.get("action") not in ACTIONS:
        raise ValueError("Invalid decision action")
    evidence = sent["evidence"]
    inner = evidence.get("observations", evidence) if isinstance(evidence, dict) else evidence
    cites = answer.get("cites") if isinstance(answer.get("cites"), list) else []
    answer["cites"] = [
        c for c in cites if str(c).strip(".[] ") and any(resolves(root, c) for root in (sent, evidence, inner))
    ]
    answer["reason"] = str(answer.get("reason") or "")
    answer["message"] = str(answer.get("message") or answer["reason"] or "No detail given.")
    if answer["action"] not in APPROVALS:
        return answer
    if any(f["flag"] == "over_role_approval_limit" for f in sent["facts"]["flags"]):
        why = f"Amount exceeds your approval limit; escalate to {sent['role_card']['escalates_to']}."
    elif not answer["cites"]:
        why = "The approval cited no evidence present in the decision input."
    elif answer["action"] == "approve_with_condition" and not str(answer.get("condition") or "").strip():
        why = "The conditional approval named no condition."
    else:
        return answer
    return {**answer, "action": "request_info", "held_from": answer["action"], "reason": why, "message": why}


def by_item(rows):
    """Lines summed per item, so a repeated line cannot hide from the comparison."""
    out = {}
    for r in rows or []:
        line = out.setdefault(r["item_code"], {"qty": 0, "rate": 0, "amount": 0})
        line["qty"] += r.get("qty", 0)
        line["amount"] += r.get("amount", 0)
        line["rate"] = max(line["rate"], r.get("rate", 0))
    return out


def facts(role, evidence, as_of, policies=()):
    """Values and contradictions code can check. Absent evidence produces no fact and no flag."""
    values, flags = {}, []
    seen = evidence.get("observations", evidence) if isinstance(evidence, dict) else {}

    def flag(name, detail):
        flags.append({"flag": name, "detail": detail})

    rows = seen.get("items") or []
    lines = by_item(rows)
    total = seen.get("grand_total")
    quote = seen.get("supplier_quote")
    if quote:
        quoted = by_item(quote.get("items"))
        if seen.get("supplier") != quote.get("supplier"):
            flag("quote_supplier_mismatch", f"PO {seen.get('supplier')}, quote {quote.get('supplier')}")
        if quote.get("valid_till") and quote["valid_till"] < as_of:
            flag("quote_expired", f"valid till {quote['valid_till']}, today {as_of}")
        if "items" in quote:
            deltas = []
            for code in sorted(set(lines) | set(quoted)):
                po, offer = lines.get(code), quoted.get(code)
                if po is None or offer is None:
                    flag("quote_line_mismatch", f"{code} is on the {'quote' if po is None else 'PO'} only")
                    continue
                delta = {k: round(po[k] - offer[k], 4) for k in po}
                deltas.append({"item_code": code, **delta})
                if any(delta.values()):
                    flag("quote_line_mismatch", f"{code} PO minus quote: {delta}")
            values["po_minus_quote_lines"] = deltas
        if total is not None and quote.get("grand_total") is not None:
            values["po_minus_quote_total"] = round(total - quote["grand_total"], 2)
            if values["po_minus_quote_total"]:
                flag("quote_total_mismatch", f"PO {total}, quote {quote['grand_total']}")
    for row in rows:
        if row.get("qty", 1) <= 0 or row.get("rate", 1) <= 0:
            flag("nonpositive_line", f"{row['item_code']} qty {row.get('qty')} rate {row.get('rate')}")
    if total is not None and rows and all("amount" in r for r in rows):
        summed = round(sum(r["amount"] for r in rows), 2)
        net = seen.get("net_total", total)  # grand_total also carries taxes and charges
        if abs(summed - net) > 0.01:
            flag("total_not_sum_of_lines", f"lines sum to {summed}, order net total {net}")

    usage = seen.get("observed_daily_usage") or {}
    stock = []
    for row in seen.get("coverage") or []:
        code = row["item_code"]
        released = row.get("released_raw_materials_qty", 0)
        other = row.get("open_po_qty_excluding_this", 0)
        held = row.get("quarantine_qty", 0)
        daily, target = usage.get(code, 0), row.get("reorder_target_qty", 0)
        fact = {"item_code": code, "released_qty": released, "quarantine_qty": held, "other_open_po_qty": other}
        if daily > 0:
            fact["days_of_cover_released"] = round(released / daily, 1)
            fact["days_of_cover_with_quarantine_and_open_po"] = round((released + held + other) / daily, 1)
        if target > 0:
            fact.update(reorder_target_qty=target, released_and_open_minus_target=round(released + other - target, 2))
            if released + other >= target:
                flag(
                    "stock_above_reorder_target",
                    f"{code} released {released} + other open {other} >= target {target}",
                )
        if other > 0:
            flag("duplicate_open_order", f"{code} already has {other} on other open purchase orders")
        stock.append(fact)
    if stock:
        values["stock"] = stock

    change = seen.get("requested_change")
    if change:
        code, level = change["item_code"], change["requested_reorder_level"]
        released, daily = change.get("released_qty", 0), change.get("observed_daily_usage", 0)
        current = change.get("current_reorder_level", 0)
        fact = {"released_minus_requested_level": round(released - level, 2), "level_increase": level - current}
        if daily > 0:
            fact["days_of_cover_released"] = round(released / daily, 1)
            fact["days_of_usage_in_requested_level"] = round(level / daily, 1)
        values["reorder_change"] = fact
        if released >= level:
            flag("stock_at_or_above_requested_level", f"{code} has {released} released, requested level {level}")
        if level <= current:
            flag("requested_level_not_above_current", f"{code} requested {level}, current {current}")
        if change.get("has_supplier") is False:
            flag("item_has_no_supplier", f"{code} has no approved supplier, so a reorder level orders nothing")

    balances = seen.get("native_balances") or seen.get("native_account_balances")
    if balances:
        values["ledger_sum"] = round(sum(balances.values()), 2)
        if abs(values["ledger_sum"]) > 0.01:
            flag("ledger_not_balanced", f"account balances sum to {values['ledger_sum']}, expected 0")

    if total is not None:
        limit = limits(role).get("po_approval_eur")
        if limit is not None:
            values["role_approval_limit_headroom"] = round(limit - total, 2)
            if total > limit:
                flag("over_role_approval_limit", f"grand_total {total}, {role} limit {limit}")
        scoped = [
            p["approval_limit"]
            for p in policies
            if seen.get("supplier") in p.get("suppliers", []) and lines and set(lines) <= set(p.get("items", []))
        ]
        if scoped:
            # Same selection as Engine.choose: the last policy that covers the total, else the last in scope.
            covering = [limit for limit in scoped if total <= limit]
            policy_limit = (covering or scoped)[-1]
            values["policy_approval_limit_headroom"] = round(policy_limit - total, 2)
            if total > policy_limit:
                flag(
                    "over_policy_approval_limit",
                    f"grand_total {total} exceeds the standing policy limit {policy_limit}",
                )
    return {**values, "flags": flags}


def prompt(role, as_of, evidence, question, policies=()):
    return {
        "role": role,
        "as_of": as_of,
        "question": question,
        "evidence": evidence,
        "facts": facts(role, evidence, as_of, policies),
        "role_card": role_card(role),
        "policy": {"effective_policies": list(policies)},
        "instruction": INSTRUCTION,
    }


def complete(content, log, *, model=None, before=None, after=None):
    """One JSON completion with no tools. Raises without credentials; nothing is approved by default.

    before runs just ahead of the paid request (spend ceiling) and after receives the raw reply (spend record).
    """
    endpoint = None if os.environ.get("NORVALE_OFFLINE") == "1" else long_run.model_endpoint()
    if not endpoint:
        raise RuntimeError("LLM judgment requires credentials; no approval fallback")
    key, base, default = endpoint
    model = model or default
    if before:
        before()
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": json.dumps(content)}],
        "response_format": {"type": "json_object"},
        "max_tokens": 1600,
    }
    log("model_request", model=model, input=content)
    request = urllib.request.Request(
        base + "/chat/completions",
        json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    from environments.norvale.native.router import provider_json

    raw = provider_json("Model", request, lambda attempt: log("model_http_attempt", attempt=attempt))
    log("model_response", response=raw)
    if after:
        after(raw)
    if raw["choices"][0].get("finish_reason") != "stop":
        raise RuntimeError("Incomplete model response; raw response retained for diagnosis")
    return json.loads(raw["choices"][0]["message"]["content"])
