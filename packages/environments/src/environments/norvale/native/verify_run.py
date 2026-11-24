"""Live readback checks and separate private/observable exports; no extraction harness."""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from environments.norvale.native.company import ACTIVE_RUN, Engine, ErpError, observable, save
from environments.norvale.native.systems_v2 import Tickets, export_native

run = Path(sys.argv[1]) if len(sys.argv) > 1 else ACTIVE_RUN
e = Engine(run)
assert e.step() is None, "Run horizon incomplete"
trace = [json.loads(x) for x in (run / "trace.jsonl").read_text().splitlines()]
native = {key: e.get(key) for key in e.docs}
ops = e.state["ops"]
checks = {}
precision = int(e.admin.get("System Settings", "System Settings").get("float_precision") or 3)
tolerance = 0.5 * 10**-precision + 1e-8
perday = defaultdict(float)
produced = defaultdict(float)
shipped = defaultdict(float)
for key, p in ops["production"].items():
    wo = native[key]
    entry = native["manufacture:" + key]
    card = native["job-card:" + key]
    assert wo["docstatus"] == entry["docstatus"] == card["docstatus"] == 1
    assert (
        wo["sales_order"] == native[p["order"]]["name"]
        and entry["work_order"] == wo["name"]
        and card["work_order"] == wo["name"]
    )
    actual = {r["item_code"]: r["qty"] for r in entry["items"] if not r.get("is_finished_item")}
    for r in native["bom:" + p["item"]]["items"]:
        assert abs(actual[r["item_code"]] - r["qty"] * p["qty"]) <= tolerance
    assert abs(card["total_time_in_mins"] - p["qty"] * e.config["shift_minutes"] / e.config["capacity_units"]) < 0.02
    perday[p["day"]] += p["qty"]
    produced[p["item"]] += p["qty"]
    for m in ops["maintenance"].values():
        assert not m["day"] <= p["day"] < m.get("closed", e.config["days"])
assert all(n <= e.config["capacity_units"] for n in perday.values())
for doc in native.values():
    if doc["doctype"] == "Delivery Note":
        for row in doc["items"]:
            shipped[row["item_code"]] += row["qty"]
assert all(n <= produced[k] for k, n in shipped.items())
for key, p in ops["invoices"].items():
    if p.get("paid"):
        assert native[key]["outstanding_amount"] == 0
bins = e.observe("WAREHOUSE", "Bin", {}, ["item_code", "warehouse", "actual_qty"])
assert all(r["actual_qty"] >= -1e-6 for r in bins)
try:
    e.api["BUYER"].list("GL Entry", fields=["name"])
except ErpError as ex:
    assert ex.status == 403
else:
    raise AssertionError("Buyer can read finance ledger")
for d in e.state["decisions"].values():
    text = json.dumps(d["input"])
    assert all(k not in text for k in ["arrival_due", "demand_scale", "private_regime"])
issues = Tickets(run).records()
for m in ops["maintenance"].values():
    if m.get("closed"):
        assert next(i for i in issues if i["number"] == m["ticket"])["state"] == "closed"
before = (run / "state.json").read_bytes()
e.through(e.config["days"] - 1)
assert before == (run / "state.json").read_bytes()
checks.update(
    material_conservation=True,
    job_card_labor_and_native_links=True,
    capacity_and_maintenance=True,
    shipment_conservation=True,
    paid_allocations=True,
    buyer_finance_denial=True,
    role_hidden_state_absent=True,
    completed_resume_noop=True,
)
# Opaque native IDs are extractor keys. Internal operation keys stay private.
records = {}
for doc in native.values():
    public = observable(doc)
    if doc["doctype"] == "Payment Entry":
        # Historical native reference_no holds a private operation key. Omit, do not invent a replacement.
        public.pop("reference_no", None)
        public["remarks"] = "\n".join(
            line for line in public.get("remarks", "").splitlines() if not line.startswith("Transaction reference no ")
        )
    records.setdefault(doc["doctype"], {})[doc["name"]] = public
for identity in e.state["identities"].values():
    employee = e.admin.get("Employee", identity["employee"])
    records.setdefault("Employee", {})[employee["name"]] = {
        k: employee.get(k)
        for k in [
            "doctype",
            "name",
            "employee_name",
            "user_id",
            "company_email",
            "reports_to",
            "date_of_joining",
            "status",
        ]
    }
    user = e.admin.get("User", identity["user"])
    records.setdefault("User", {})[user["name"]] = {
        k: user.get(k) for k in ["doctype", "name", "full_name", "email", "enabled"]
    }
    records["User"][user["name"]]["roles"] = [{"role": r["role"]} for r in user.get("roles", [])]
save(
    run / "export/observable/export-notes.json",
    {
        "omissions": [
            "Private custom_norvale_operation fields",
            "Payment Entry reference_no and corresponding remarks line (historical internal operation references)",
            "User authentication fields; Employee personal demographics",
        ],
        "dates": "ERP transaction dates are business dates; native creation/modified timestamps are wall-clock dates.",
    },
)
save(run / "export/observable/native-records.json", records)
save(run / "export/observable/stock-balances.json", bins)
ledger = e.observe(
    "FINANCE",
    "GL Entry",
    {"company": e.company},
    ["name", "posting_date", "account", "debit", "credit", "voucher_type", "voucher_no"],
)
assert len(ledger) < 10000, "GL export reached query limit; paginate before release"
assert abs(sum(r["debit"] - r["credit"] for r in ledger)) < 0.01
checks["general_ledger_balanced"] = True
save(run / "export/observable/general-ledger.json", ledger)
stock_ledger = e.observe(
    "WAREHOUSE",
    "Stock Ledger Entry",
    {"company": e.company},
    [
        "name",
        "posting_date",
        "posting_time",
        "item_code",
        "warehouse",
        "actual_qty",
        "qty_after_transaction",
        "voucher_type",
        "voucher_no",
    ],
)
assert len(stock_ledger) < 10000, "Stock export reached query limit; paginate before release"
save(run / "export/observable/stock-ledger.json", stock_ledger)
mail = [
    {"day": r["day"], "sender": r["sender"], "recipient": r["recipient"], "message": r["message"]}
    for r in trace
    if r["kind"] == "native_mail_readback"
]
save(run / "export/observable/mail.json", mail)
business_issues = [dict(i) for i in issues if i["number"] in {m["ticket"] for m in ops["maintenance"].values()}]
for issue in business_issues:
    issue["comments"] = Tickets(run).request("GET", f"/repos/controller/operations/issues/{issue['number']}/comments")
save(run / "export/observable/tickets.json", business_issues)
export_native(run)
summary = {
    "days": e.config["days"],
    "completed_stages": len(e.state["completed"]),
    "orders": len(ops["orders"]),
    "products_ordered": len({r["item"] for r in ops["orders"].values()}),
    "purchases": len(ops["purchases"]),
    "production_jobs": len(ops["production"]),
    "produced_units": ops["meter"],
    "shipped_units": sum(shipped.values()),
    "maintenance_jobs": len(ops["maintenance"]),
    "native_documents": len(native),
    "decision_modes": dict(Counter(d["mode"] for d in e.state["decisions"].values())),
    "jev_api_calls": sum(r["kind"] == "jev_route" and "response" in r for r in trace),
    "model_usage": [r["response"].get("usage", {}) for r in trace if r["kind"] == "model_response"],
    "policies": len(e.state.get("standing_policies", [])),
    "failures": sum(r["kind"] == "failed" for r in trace),
    "mail_readbacks": len(mail),
    "checks": checks,
}
summary["routing_scope"] = (
    "Uncached Engine.choose role decisions: purchasing, reorder changes and quarterly management. Routine operational APIs do not pass through JEV. Policy-unmatched cases bypassed JEV before 2027-02-04; historical decisions are preserved."
)
summary["decisions_by_role_and_mode"] = dict(
    Counter(d["input"]["role"] + ":" + d["mode"] for d in e.state["decisions"].values())
)
summary["ordered_units"] = sum(r["qty"] for r in ops["orders"].values())
summary["unshipped_units_at_cutoff"] = summary["ordered_units"] - summary["shipped_units"]
summary["shipped_fraction_of_all_ordered_units"] = (
    summary["shipped_units"] / summary["ordered_units"] if summary["ordered_units"] else None
)
summary["implemented_master_changes"] = sum(r["kind"] == "master_implemented" for r in trace)
summary["routing_correction_day"] = 34
summary["model_requests"] = sum(r["kind"] == "model_request" for r in trace)
summary["model_responses"] = sum(r["kind"] == "model_response" for r in trace)
summary["model_http_attempts"] = sum(r["kind"] == "model_http_attempt" for r in trace)
summary["jev_http_attempts"] = sum(r["kind"] == "jev_http_attempt" for r in trace)
usage = summary["model_usage"]
summary["reported_usage_totals"] = {
    k: sum(u.get(k, 0) or 0 for u in usage) for k in ["prompt_tokens", "completion_tokens", "total_tokens"]
}
summary["reported_model_cost"] = (
    sum(u.get("cost", 0) or 0 for u in usage) if usage and all("cost" in u for u in usage) else None
)
summary["cost_reporting_limit"] = (
    "JEV response and provider usage retained; absent provider cost fields are unknown, never assumed free."
)
private = run / "private"
private.mkdir(exist_ok=True, mode=0o700)
save(private / "native-crosswalk.json", e.docs)
(private / "native-crosswalk.json").chmod(0o600)
with (private / "reference-evidence.jsonl").open("w") as stream:
    for i, r in enumerate(trace, 1):
        if r["kind"] in [
            "private_regime",
            "policy_authorized",
            "policy_rejected",
            "decision",
            "observation",
            "readback",
            "failed",
            "native_mail_readback",
            "ticket_readback",
            "physical_arrival",
            "physical_production",
            "physical_dispatch",
            "master_implemented",
            "implementation_change",
            "jev_route",
            "model_response",
            "stage_retry",
            "policy_proposal_rejected",
        ]:
            stream.write(
                json.dumps(
                    {
                        "evidence_id": f"E{i:07}",
                        "trace_line": i,
                        "visibility": "private-runtime",
                        "causal_status": "execution provenance, not identified causal conclusion",
                        **r,
                    }
                )
                + "\n"
            )
(private / "reference-evidence.jsonl").chmod(0o600)
save(run / "checks.json", checks)
save(run / "summary.json", summary)
print(json.dumps({k: v for k, v in summary.items() if k != "model_usage"}, indent=2))
