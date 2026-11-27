"""Role contracts (docs/BEHAVIOUR.md): one registry and one generic daily phase.

A contract says what opens a case, what the role may choose, and which native write each choice makes.
`run` is the only phase: for each contract it opens at most one case per record and version, decides it
(Engine.choose; the offline rule when NORVALE_OFFLINE=1), executes the chosen action's write with readback,
and escalates (once) cases that stay open past their due day, still retrying them. Cases live in engine.state["role_cases"].
"""

import json
from datetime import date

from components.erp_client import ErpError
from components.qms import CASE, INSPECTION, APIError

WINDOW_DAYS = 14  # repeated failures on one item count inside this window
PREFIX = "case:"


def day_of(engine, text):
    """Business day number of an ISO date, relative to the engine calendar."""
    return (date.fromisoformat(text[:10]) - date.fromisoformat(engine.dated(0))).days


def flagged(facts, *names):
    return any(f["flag"] in names for f in facts["flags"])


# ---------------------------------------------------------------- QA_MGR: non-conformances


def qa_actor(engine, batch):
    """The QA approver client on the QMS site that holds this lot's records (the site review() uses)."""
    from environments.norvale.integrations.company_run.operating import Operating

    op = Operating(engine)
    lot = engine.company_erp.state["lots"][batch]
    lab = op.registered_lab(lot)
    primary = None if lab else op.primary_sample(lot)
    if lab:
        return lab.actor("approver")
    return primary.actor("approver") if primary else op.retest_actor("approver")


def nonconformances(engine):
    """Every non-conformance on a lot with a recorded final outcome (rejected), read from the QMS."""
    rows = []
    for batch in sorted(engine.state.get("operating_lot_rejections", {})):
        item = engine.company_erp.state["lots"][batch]["item"]
        found = qa_actor(engine, batch).list(
            CASE,
            {"custom_qms_batch": batch},
            fields=("name", "creation", "modified", "status", "custom_failed_inspection"),
        )
        rows += [{**row, "batch": batch, "item": item} for row in found]
    return rows


def qa_trigger(engine):
    return [
        dict(record=n["name"], version=n["creation"], batch=n["batch"], item=n["item"], created=n["creation"][:10])
        for n in nonconformances(engine)
        if n["status"] == "Open"
    ]


def decided(engine, contract, **match):
    return [
        c
        for c in engine.state.get("role_cases", {}).values()
        if c["contract"] == contract
        and c["status"] in ("decided", "open", "escalated")
        and all(c["context"].get(k) == v for k, v in match.items())
    ]


def qa_evidence(engine, case):
    ctx = case["context"]
    qa = qa_actor(engine, ctx["batch"])
    nc = qa.get(CASE, case["record"])
    inspection = qa.get(INSPECTION, nc["custom_failed_inspection"])
    same_item = [
        n["name"]
        for n in nonconformances(engine)
        if n["item"] == ctx["item"] and engine.day - day_of(engine, n["creation"]) <= WINDOW_DAYS
    ]
    pending = [c for c in decided(engine, "design_change", item=ctx["item"]) if c["status"] != "decided"]
    escalated = [
        c
        for c in decided(engine, "nonconformance", item=ctx["item"])
        if c.get("action") == "escalate" and engine.day - c["decided"] <= WINDOW_DAYS
    ]
    return dict(
        record=dict(doctype=CASE, name=nc["name"], subject=nc.get("subject"), status=nc["status"]),
        inspection=dict(name=inspection["name"], workflow_state=inspection.get("workflow_state")),
        batch=ctx["batch"],
        item=ctx["item"],
        same_item_nonconformances=same_item,
        engineering_review_open=bool(pending),
        escalated_recently=bool(escalated),
    )


def qa_facts(evidence, today):
    flags = []
    final = evidence["inspection"]["workflow_state"] == "Lot Rejected"
    if not final:
        flags.append(
            dict(flag="lot_outcome_not_final", detail=f"inspection is {evidence['inspection']['workflow_state']}")
        )
    if evidence["engineering_review_open"]:
        flags.append(dict(flag="engineering_review_pending", detail="process engineering has not decided on this item"))
    count = len(evidence["same_item_nonconformances"])
    if count >= 3 and not evidence["escalated_recently"]:
        flags.append(dict(flag="repeat_failures_unescalated", detail=f"{count} non-conformances on {evidence['item']}"))
    return dict(lot_final=final, same_item_count=count, flags=flags)


def qa_rule(evidence):
    facts = evidence["facts"]
    if flagged(facts, "lot_outcome_not_final", "engineering_review_pending"):
        return "keep_open", "A blocking fact is open: " + ", ".join(f["flag"] for f in facts["flags"]) + "."
    if flagged(facts, "repeat_failures_unescalated"):
        return (
            "escalate",
            f"{facts['same_item_count']} failures on one item inside {WINDOW_DAYS} days need the plant manager.",
        )
    return "close", "The lot is rejected and nothing else is open on this item."


def qa_close(engine, case, evidence, out):
    if evidence.get("engineering_review_open"):  # a model choice, or a retry replaying an earlier one
        raise RuntimeError("engineering review is still pending on this item")
    qa = qa_actor(engine, evidence["batch"])
    qa.update(
        CASE,
        case["record"],
        status="Resolved",
        corrective_action=f"Lot rejected; closed {engine.date()}: {out['reason']}",
    )
    if qa.get(CASE, case["record"])["status"] != "Resolved":
        raise RuntimeError("QMS readback: non-conformance is not Resolved")


def qa_keep(engine, case, evidence, out):
    note = f"Kept open {engine.date()}: {out['reason']}"
    qa = qa_actor(engine, evidence["batch"])
    qa.update(CASE, case["record"], preventive_action=note)
    if qa.get(CASE, case["record"]).get("preventive_action") != note:
        raise RuntimeError("QMS readback: review note was not saved")


def mail_escalation(engine, case, out, sender, recipient):
    engine.notify(
        "escalation:" + case["key"],
        sender,
        recipient,
        f"Escalation on {case['record']}",
        f"{case['record']} needs you.",
        out["message"],
    )


def qa_escalate(engine, case, evidence, out):
    mail_escalation(engine, case, out, "QA_MGR", "PLANT_MGR")


# ---------------------------------------------------------------- CFO: supplier invoices


def invoice_trigger(engine):
    rows = engine.observe(
        "FINANCE",
        "Purchase Invoice",
        {"docstatus": 1, "outstanding_amount": [">", 0]},
        ["name", "grand_total", "posting_date", "supplier"],
    )
    return [dict(record=r["name"], version=f"{r['grand_total']}:{r['posting_date']}") for r in rows]


def invoice_evidence(engine, case):
    from environments.norvale.native import judgment

    api = engine.api["FINANCE"]
    invoice = api.get("Purchase Invoice", case["record"])
    orders = sorted({r["purchase_order"] for r in invoice["items"] if r.get("purchase_order")})
    receipts = sorted({r["purchase_receipt"] for r in invoice["items"] if r.get("purchase_receipt")})
    order = api.get("Purchase Order", orders[0]) if orders else None
    received = {}
    for name in receipts:
        for item, line in judgment.by_item(api.get("Purchase Receipt", name)["items"]).items():
            received[item] = received.get(item, 0) + line["qty"]
    return dict(
        record=dict(doctype="Purchase Invoice", name=invoice["name"]),
        invoice=dict(
            supplier=invoice["supplier"],
            grand_total=invoice["grand_total"],
            due_date=invoice.get("due_date"),
            on_hold=invoice.get("on_hold", 0),
            billed={k: v["qty"] for k, v in judgment.by_item(invoice["items"]).items()},
        ),
        purchase_orders=orders,
        order=order and dict(name=order["name"], supplier=order["supplier"], grand_total=order["grand_total"]),
        received=received,
    )


def invoice_facts(evidence, today):
    flags, inv, order = [], evidence["invoice"], evidence["order"]
    if not order:
        flags.append(dict(flag="no_purchase_order", detail="the invoice cites no purchase order"))
    else:
        if order["supplier"] != inv["supplier"]:
            flags.append(dict(flag="supplier_mismatch", detail=f"PO {order['supplier']}, invoice {inv['supplier']}"))
        if inv["grand_total"] > order["grand_total"] + 0.01:
            flags.append(
                dict(flag="billed_over_order", detail=f"billed {inv['grand_total']}, ordered {order['grand_total']}")
            )
    for item, qty in inv["billed"].items():
        if qty > evidence["received"].get(item, 0):
            flags.append(
                dict(
                    flag="billed_over_received",
                    detail=f"{item} billed {qty}, received {evidence['received'].get(item, 0)}",
                )
            )
    return dict(flags=flags)


def invoice_rule(evidence):
    facts = evidence["facts"]
    if flagged(facts, "no_purchase_order", "supplier_mismatch"):
        return "request_info", "The invoice does not tie to a purchase order from this supplier."
    if flagged(facts, "billed_over_order", "billed_over_received"):
        return "hold", "The invoice bills more than was ordered or received."
    return "approve", "Invoice ties to the order and the receipt."


def comment(engine, case, text):
    return engine.write(
        "comment:" + case["key"],
        "Comment",
        dict(comment_type="Comment", reference_doctype="Purchase Invoice", reference_name=case["record"], content=text),
        "FINANCE",
    )


def hold_method(engine, case, method, **args):
    api = engine.api["FINANCE"]
    doc = api.get("Purchase Invoice", case["record"])
    api.call("run_doc_method", {"docs": json.dumps(doc), "method": method, "args": json.dumps(args)})
    return api.get("Purchase Invoice", case["record"])


def invoice_approve(engine, case, evidence, out):
    if evidence["invoice"]["on_hold"] and hold_method(engine, case, "unblock_invoice")["on_hold"]:
        raise RuntimeError("ERP readback: invoice is still on hold")
    comment(engine, case, f"Approved for payment {engine.date()}: {out['reason']}")


def invoice_hold(engine, case, evidence, out):
    if not hold_method(engine, case, "block_invoice", hold_comment=out["reason"])["on_hold"]:
        raise RuntimeError("ERP readback: invoice is not on hold")


def invoice_ask(engine, case, evidence, out):
    comment(engine, case, f"Information requested {engine.date()}: {out['message']}")
    engine.notify(
        "ask:" + case["key"],
        "CFO",
        "BUYER",
        f"Invoice {case['record']}: information needed",
        f"{case['record']}.",
        out["message"],
    )


def invoice_cleared(engine, key):
    """Whether a supplier invoice may be paid: its latest finance case approved it. Sales invoices are not gated."""
    saved = engine.docs.get(key)
    if not saved or saved["doctype"] != "Purchase Invoice":
        return True
    mine = [
        c
        for c in engine.state.get("role_cases", {}).values()
        if c["contract"] == "invoice" and c["record"] == saved["name"]
    ]
    return bool(mine) and max(mine, key=lambda c: c["opened"]).get("action") == "approve"


# ---------------------------------------------------------------- PLANNER: shortages


def shortage_trigger(engine):
    today = engine.state["ops"].get("shortage_context", {})
    return [
        dict(record=item, version=engine.date(), **ctx)
        for item, ctx in sorted(today.items())
        if ctx["day"] == engine.day
    ]


def shortage_evidence(engine, case):
    item, ctx = case["record"], case["context"]
    bins = engine.observe(
        "PLANNER", "Bin", {"item_code": item, "warehouse": engine.wh["rm"]}, ["item_code", "actual_qty"]
    )
    return dict(
        record=dict(doctype="Item", name=item),
        released_qty=sum(b["actual_qty"] for b in bins),
        shortage_days=engine.state["ops"]["shortage_days"].get(item, []),
        work_planned_today=ctx["work_planned"],
    )


def shortage_facts(evidence, today):
    days, flags = evidence["shortage_days"], []
    streak = 0
    for d in reversed(days):
        if streak and d != days[-streak] - 1:
            break
        streak += 1
    if streak >= 2:
        flags.append(dict(flag="repeated_shortage", detail=f"short on {streak} consecutive days"))
    if not evidence["work_planned_today"]:
        flags.append(dict(flag="nothing_else_to_build", detail="no other work order could be planned today"))
    return dict(streak=streak, flags=flags)


def shortage_rule(evidence):
    facts = evidence["facts"]
    if flagged(facts, "repeated_shortage"):
        return "escalate", f"Short for {facts['streak']} days running; supply chain must replenish."
    if not flagged(facts, "nothing_else_to_build"):
        return "reschedule", "Other orders can be built while this item is short."
    return "wait", "First day short and nothing else to build; recheck tomorrow."


def todo(engine, case, to, text, **extra):
    engine.write(
        "todo:" + case["key"],
        "ToDo",
        dict(description=text, reference_type="Item", reference_name=case["record"], allocated_to=to, **extra),
        "PLANNER",
    )


def shortage_wait(engine, case, evidence, out):
    todo(engine, case, "planner@norvale.test", out["message"], date=engine.dated(engine.day + 1))


def shortage_reschedule(engine, case, evidence, out):
    todo(engine, case, "production@norvale.test", out["message"], date=engine.dated(engine.day + 1))


def shortage_escalate(engine, case, evidence, out):
    todo(engine, case, "scm_mgr@norvale.test", out["message"], date=engine.dated(engine.day + 1))


# ---------------------------------------------------------------- PROC_ENG: repeated failures


def design_trigger(engine):
    by_item = {}
    for n in nonconformances(engine):
        if engine.day - day_of(engine, n["creation"]) <= WINDOW_DAYS:
            by_item.setdefault(n["item"], []).append(n)
    return [
        dict(record=item, item=item, version=",".join(sorted(n["name"] for n in rows)))
        for item, rows in sorted(by_item.items())
        if len(rows) >= 2
    ]


def design_evidence(engine, case):
    item = case["record"]
    names = sorted(
        n["name"]
        for n in nonconformances(engine)
        if n["item"] == item and engine.day - day_of(engine, n["creation"]) <= WINDOW_DAYS
    )
    raised = [
        c for c in decided(engine, "design_change") if c["record"] == item and c.get("action") == "raise_design_change"
    ]
    return dict(
        record=dict(doctype="Item", name=item),
        nonconformances=names,
        change_raised_recently=any(engine.day - c["decided"] <= WINDOW_DAYS for c in raised),
    )


def design_facts(evidence, today):
    flags = []
    if evidence["change_raised_recently"]:
        flags.append(
            dict(flag="change_already_raised", detail="a design change for this item was raised inside the window")
        )
    return dict(count=len(evidence["nonconformances"]), flags=flags)


def design_rule(evidence):
    facts = evidence["facts"]
    if flagged(facts, "change_already_raised"):
        return "no_change", "A change for this item is already in flight."
    if facts["count"] >= 3:
        return "raise_design_change", f"{facts['count']} non-conformances on one item in {WINDOW_DAYS} days."
    return "no_change", f"{facts['count']} non-conformances: watch, the threshold to change the design is 3."


def design_raise(engine, case, evidence, out):
    # The PLM change path (definition_change.propose) needs a revised ERP BOM that nothing in the engine drafts yet.
    # Until it does, the request is a task for the planner, who owns BOM drafting in ERP.
    todo(engine, case, "planner@norvale.test", f"Design change requested for {case['record']}: {out['message']}")


def design_none(engine, case, evidence, out):
    todo(
        engine,
        case,
        "planner@norvale.test",
        f"No design change for {case['record']}: {out['message']}",
        status="Closed",
    )


# ---------------------------------------------------------------- registry


def contract(name, role, owns, trigger, evidence, facts, actions, rule, due_days, escalates_to, question, **more):
    return dict(
        name=name,
        role=role,
        owns=owns,
        trigger=trigger,
        evidence=evidence,
        facts=facts,
        actions=actions,
        offline_rule=rule,
        due_days=due_days,
        escalates_to=escalates_to,
        question=question,
        identity=role,
        **more,
    )


CONTRACTS = [
    contract(
        "design_change",
        "PROC_ENG",
        "Item",
        design_trigger,
        design_evidence,
        design_facts,
        dict(raise_design_change=design_raise, no_change=design_none),
        design_rule,
        5,
        "QA_MGR",
        "Several non-conformances hit one item. Raise a design change or make no change.",
    ),
    contract(
        "nonconformance",
        "QA_MGR",
        "Non Conformance",
        qa_trigger,
        qa_evidence,
        qa_facts,
        dict(close=qa_close, keep_open=qa_keep, escalate=qa_escalate),
        qa_rule,
        2,
        "PLANT_MGR",
        "This non-conformance's lot has a final outcome. Close it, keep it open, or escalate to the plant manager.",
        revisit=("keep_open", "escalate"),
        review_days=7,
        closes=("close",),
    ),
    contract(
        "invoice",
        "CFO",
        "Purchase Invoice",
        invoice_trigger,
        invoice_evidence,
        invoice_facts,
        dict(approve=invoice_approve, hold=invoice_hold, request_info=invoice_ask),
        invoice_rule,
        3,
        "CEO",
        "Decide whether this supplier invoice is approved for payment, held, or needs information.",
        approvals=("approve",),
        revisit=("hold", "request_info"),
        review_days=7,
        closes=("approve",),
    ),
    contract(
        "shortage",
        "PLANNER",
        "Item",
        shortage_trigger,
        shortage_evidence,
        shortage_facts,
        dict(wait=shortage_wait, reschedule=shortage_reschedule, escalate=shortage_escalate),
        shortage_rule,
        1,
        "SC_MGR",
        "Released stock is short for planned work. Wait, reschedule other work, or escalate to supply chain.",
    ),
]
BY_NAME = {c["name"]: c for c in CONTRACTS}


def offline_rule(key, evidence):
    """Deterministic choice for `case:<contract>:...` keys, from the code-computed facts in the evidence."""
    return BY_NAME[key.split(":")[1]]["offline_rule"](evidence)


def message(case, out):
    return f"{case['role']} {out['action'].replace('_', ' ')} on {case['record']}: {out['message']}"


# ---------------------------------------------------------------- the one generic phase


def open_cases(engine, c, cases):
    for rec in c["trigger"](engine):
        mine = [x for x in cases.values() if x["contract"] == c["name"] and x["record"] == rec["record"]]
        latest = max(mine, key=lambda x: x["opened"], default=None)
        version = rec["version"]
        if (
            latest
            and latest.get("action") in c.get("revisit", ())
            and engine.day - latest["decided"] >= c.get("review_days", 7)
        ):
            version += f"@{engine.day}"  # the record is still open after a review: a new case
        key = f"{c['name']}:{rec['record']}:{version}"
        if key in cases:
            continue
        context = {k: v for k, v in rec.items() if k not in ("record", "version")}
        cases[key] = dict(
            key=key,
            contract=c["name"],
            role=c["role"],
            type=c["owns"],
            record=rec["record"],
            version=version,
            opened=engine.day,
            due=engine.day + c["due_days"],
            status="open",
            context=context,
        )
        engine.log("role_case_opened", case=key, role=c["role"], record=rec["record"])


def decide(engine, c, case):
    evidence = c["evidence"](engine, case)
    evidence["facts"] = c["facts"](evidence, engine.date())
    out = engine.choose(PREFIX + case["key"], c["role"], evidence, c["question"], actions=list(c["actions"]))
    if out["action"] not in c["actions"]:
        raise ValueError(f"{c['role']} chose {out['action']!r}, which is not one of {list(c['actions'])}")
    out = {**out, "message": out.get("message") or out["reason"]}
    c["actions"][out["action"]](
        engine, case, evidence, out
    )  # raises on a failed write or readback: the case stays open
    case.update(
        status="decided", action=out["action"], reason=out["reason"], decided=engine.day, message=message(case, out)
    )
    case["approval"] = out["action"] in c.get("approvals", ())
    case.pop("posted", None)  # an escalated case that is now decided gets its own chat message
    engine.log("role_case_decided", case=case["key"], action=out["action"])


def escalate_overdue(engine, c, case):
    out = dict(
        action="escalate",
        reason="No action by the due day.",
        message=f"{case['record']} was due on day {case['due']} and is still open.",
    )
    engine.notify(
        "overdue:" + case["key"],
        c["role"],
        c["escalates_to"],
        f"Overdue: {case['record']}",
        f"{case['record']} is overdue.",
        out["message"],
    )
    case.update(status="escalated", escalated=engine.day, message=message(case, out))
    engine.log("role_case_escalated", case=case["key"], to=c["escalates_to"])


def run(engine):
    cases = engine.state.setdefault("role_cases", {})
    for c in CONTRACTS:
        open_cases(engine, c, cases)
        live = ("open", "escalated")  # an escalated case is still undone: keep retrying it
        for case in [x for x in cases.values() if x["contract"] == c["name"] and x["status"] in live]:
            try:
                decide(engine, c, case)
            except (
                RuntimeError,
                OSError,
                KeyError,
                ValueError,
                ErpError,
                APIError,
            ) as exc:  # leave the case open for retry; it is reported
                engine.log("role_case_failed", case=case["key"], error=str(exc))
            if case["status"] == "open" and engine.day > case["due"]:
                escalate_overdue(engine, c, case)
            engine.persist()
