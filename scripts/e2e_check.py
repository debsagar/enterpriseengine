#!/usr/bin/env python3
"""End-to-end check of a finished Norvale run directory.

    uv run python scripts/e2e_check.py /data/runs/<run_id> [--behaviour] [--json report.json]

Software checks read only <run>/observable plus verification.json: records per system and day, no undated
rows, one distinct snapshot per day, and linked records through every sell, buy, make and design step.
Behaviour checks also read the private trace: every decision's evidence records must be in the lake and
approved purchases must be submitted there. The exit status reflects the software checks; --behaviour
adds the behaviour checks.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from environments.norvale.integrations.lake.adapter import Lake
from environments.norvale.verify_layout import business_days

SYSTEMS = ("erp", "qms", "mes", "plm", "mail", "calendar", "matrix", "master", "tickets")


def system_of(source):
    suffix = source.rsplit("-", 1)[-1]
    return suffix if suffix in SYSTEMS else "mes"


def load(run):
    rows = Lake(run / "observable/lake/delta", {})._rows()
    for row in rows:
        row["doc"] = json.loads(row["payload"])
    return rows


def latest(rows):
    """{entity: {native id: newest document}}."""
    state = defaultdict(dict)
    for row in sorted(rows, key=lambda r: r["ingested_at"]):
        state[row["entity"]][row["native_id"]] = row["doc"]
    return state


def lines(doc, key="items"):
    return doc.get(key) or []


def submitted(docs):
    return {name: doc for name, doc in docs.items() if doc.get("docstatus") == 1}


def chains(state):
    """{chain: [(step, how many records link back to the step before)]}."""

    def get(entity):
        return submitted(state.get(entity, {}))

    def following(entity, before, field, table="items"):
        return {
            name: doc for name, doc in get(entity).items() if any(row.get(field) in before for row in lines(doc, table))
        }

    sales = get("Sales Order")
    deliveries = following("Delivery Note", sales, "against_sales_order")
    invoices = following("Sales Invoice", deliveries, "delivery_note")
    receipts_in = following("Payment Entry", invoices, "reference_name", "references")

    orders = get("Purchase Order")
    receipts = following("Purchase Receipt", orders, "purchase_order")
    bills = following("Purchase Invoice", receipts, "purchase_receipt")
    paid = following("Payment Entry", bills, "reference_name", "references")

    # Each step keeps only records that follow from the step before it.
    work = get("Work Order")
    mes_orders = {
        name: doc for name, doc in state.get("MES Manufacturing Order", {}).items() if doc.get("origin") in work
    }
    completions = {
        name: doc
        for name, doc in state.get("mrp.production", {}).items()
        if name in mes_orders and doc.get("erp_work_ref") == mes_orders[name].get("origin")
    }
    completed = {doc["erp_work_ref"] for doc in completions.values()}
    # The output receipt that carries each completion, then the lot's inspection, disposition and release.
    outputs = {
        name: doc
        for name, doc in get("Stock Entry").items()
        if doc.get("work_order") in completed and doc.get("purpose") == "Manufacture"
    }
    batches = {row.get("batch_no") for doc in outputs.values() for row in lines(doc)} - {None, ""}
    bundles = state.get("Serial and Batch Bundle", {})
    batches |= {
        entry.get("batch_no")
        for bundle in bundles.values()
        if bundle.get("voucher_no") in outputs
        for entry in lines(bundle, "entries")
    } - {None, ""}
    inspections = {n: d for n, d in get("Quality Inspection").items() if d.get("batch_no") in batches}
    dispositions = {
        n: d for n, d in state.get("Norvale QMS Disposition", {}).items() if d.get("inspection") in inspections
    }
    released = {d.get("batch_no") for d in dispositions.values()} | {
        d.get("output_batch") for d in dispositions.values()
    }
    transfers = {
        n: d
        for n, d in get("Stock Entry").items()
        if d.get("purpose") == "Material Transfer"
        and (
            any(row.get("batch_no") in released for row in lines(d))
            or (d.get("release_disposition") or {}).get("batch") in released
        )
    }
    publications = state.get("InvenTree Publication", {})
    erp_boms = {
        n
        for n, d in publications.items()
        if (d.get("acknowledgments", {}).get("erp") or {}).get("native_id") in state.get("BOM", {})
    }
    mes_boms = {
        n
        for n, d in publications.items()
        if str((d.get("acknowledgments", {}).get("mes") or {}).get("native_id")) in state.get("MES BOM", {})
    }
    return {
        "sell": [
            ("Sales Order", len(sales)),
            ("Delivery Note", len(deliveries)),
            ("Sales Invoice", len(invoices)),
            ("Payment Entry", len(receipts_in)),
        ],
        "buy": [
            ("Purchase Order", len(orders)),
            ("Purchase Receipt", len(receipts)),
            ("Purchase Invoice", len(bills)),
            ("Payment Entry", len(paid)),
        ],
        "make": [
            ("Work Order", len(work)),
            ("MES Manufacturing Order", len(mes_orders)),
            ("MES completion", len(completions)),
            ("Manufacture Stock Entry", len(outputs)),
            ("Quality Inspection", len(inspections)),
            ("QMS Disposition", len(dispositions)),
            ("Release Stock Entry", len(transfers)),
        ],
        "design": [
            ("PLM publication", len(publications)),
            ("received as ERP BOM", len(erp_boms)),
            ("received as MES BOM", len(mes_boms)),
        ],
        "communication": [
            (entity, len(state.get(entity, {}))) for entity in ("Mail Message", "Calendar Event", "Matrix Message")
        ],
    }


def software(run, rows, report):
    failures = []
    config = json.loads((run / "run.json").read_text())["scenario"]
    days = business_days(config)
    sources = defaultdict(set)
    for row in rows:
        sources[row["entity"], row["native_id"]].add(row["source"])
    shared = [key for key, found in sources.items() if len(found) > 1 and system_of(min(found)) != "mes"]
    if shared:
        # The chain checks join records by native ID, which is only sound for one deployment per system.
        failures.append(f"{len(shared)} record IDs come from more than one source, e.g. {shared[0]}")
    undated = [row for row in rows if not row.get("business_date")]
    if undated:
        failures.append(f"{len(undated)} lake rows have no business date")
    changed = {day: Counter() for day in days}
    for row in rows:
        if row.get("business_date") in changed:
            changed[row["business_date"]][system_of(row["source"])] += 1
    known = {(row["source"], row["entity"], row["native_id"], row["content_hash"]): row for row in rows}
    snapshots, previous = {}, None
    for day in days:
        path = run / "observable/days" / day / "manifest.json"
        if not path.is_file():
            failures.append(f"{day}: no manifest")
            continue
        manifest = json.loads(path.read_text())
        pinned = {
            (source, entity, name, digest)
            for source, entities in manifest["sources"].items()
            for entity, summary in entities.items()
            for name, digest in summary.get("records", {}).items()
        }
        bad = [key for key in pinned if key not in known or known[key].get("business_date", "9999") > day]
        if bad:
            failures.append(f"{day}: {len(bad)} snapshot records are missing from the lake or dated later")
        if pinned == previous:
            failures.append(f"{day}: snapshot is identical to the previous day's")
        previous = pinned
        snapshots[day] = dict(Counter(system_of(source) for source, *_ in pinned))
    linked = chains(latest(rows))
    for chain, steps in linked.items():
        failures += [f"{chain} chain: no linked {step}" for step, count in steps if not count]
    verification = run / "verification.json"
    result = json.loads(verification.read_text()) if verification.is_file() else {}
    if not result.get("ok"):
        failures.append("verification.json is missing or not ok")
    report.update(
        days=days,
        rows=len(rows),
        undated=len(undated),
        changed_rows_per_day={day: dict(counts) for day, counts in changed.items()},
        snapshot_records_per_day=snapshots,
        chains=linked,
    )
    return failures


CHILD_TABLES = {"items", "taxes", "payment_schedule", "payment_terms", "accounts", "references", "readings", "entries"}


def references(value):
    """Native record names cited anywhere inside a decision's evidence. Child row names are not records."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("name", "report") and isinstance(item, str):
                yield item
            elif key not in CHILD_TABLES:
                yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def behaviour(run, rows, report):
    failures = []
    trace = run / "private/trace/trace.jsonl"
    events = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()] if trace.is_file() else []
    decisions = [event for event in events if event.get("kind") == "decision"]
    known = {row["native_id"] for row in rows}
    state = latest(rows)
    untraced, unexecuted = [], []
    actions, roles = Counter(), Counter()
    for event in decisions:
        sent, out = event["input"], event["output"]
        actions[out["action"]] += 1
        roles[sent["role"]] += 1
        cited = set(references(sent.get("evidence", {})))
        missing = sorted(cited - known)
        if not cited or missing:
            untraced.append(dict(event=event.get("event"), role=sent["role"], missing=missing[:5], cited=len(cited)))
        evidence = sent.get("evidence", {})
        evidence = evidence.get("observations", evidence)
        name = evidence.get("name")
        if "supplier" in evidence:
            if out["action"] in ("approve", "approve_with_condition"):
                if (state.get("Purchase Order", {}).get(name) or {}).get("docstatus") != 1:
                    unexecuted.append(str(name))
    mails = state.get("Mail Message", {})
    if not decisions:
        failures.append("no decisions were made")
    if untraced:
        failures.append(f"{len(untraced)} decisions cite records the lake does not hold")
    if unexecuted:
        failures.append(f"{len(unexecuted)} approved purchases are not submitted in the lake")
    if not mails:
        failures.append("no email was sent")
    report["behaviour"] = dict(
        decisions=len(decisions),
        modes=dict(Counter(event.get("mode") for event in decisions)),
        roles=dict(roles),
        actions=dict(actions),
        untraced=untraced[:10],
        unexecuted=unexecuted[:10],
        emails=len(mails),
    )
    return failures


def table(title, days, values):
    print(f"\n{title}")
    columns = [s for s in SYSTEMS if any(s in values.get(day, {}) for day in days)]
    print("date       " + "".join(f"{c:>9}" for c in columns) + "    total")
    for day in days:
        row = values.get(day, {})
        print(f"{day} " + "".join(f"{row.get(c, 0):>9}" for c in columns) + f"{sum(row.values()):>9}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path)
    parser.add_argument("--behaviour", action="store_true", help="Also fail on the behaviour checks")
    parser.add_argument("--json", type=Path, help="Write the full report here")
    args = parser.parse_args(argv)
    rows = load(args.run)
    report = dict(run=str(args.run))
    failed = software(args.run, rows, report)
    behaviour_failed = behaviour(args.run, rows, report)
    table("New or changed lake rows per system", report["days"], report["changed_rows_per_day"])
    table("Records in each day's snapshot", report["days"], report["snapshot_records_per_day"])
    print(f"\nLake rows: {report['rows']}; undated: {report['undated']}")
    for chain, steps in report["chains"].items():
        complete = "complete" if all(count for _, count in steps) else "BROKEN"
        print(f"{chain:14} {complete:9}" + " -> ".join(f"{step} {count}" for step, count in steps))
    print("\nBehaviour:", json.dumps({k: v for k, v in report["behaviour"].items() if k not in ("untraced",)}))
    report.update(software_failures=failed, behaviour_failures=behaviour_failed)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n")
    for kind, failures in (("SOFTWARE", failed), ("BEHAVIOUR", behaviour_failed)):
        print(f"\n{kind}: {'PASS' if not failures else 'FAIL'}")
        for failure in failures:
            print("  -", failure)
    return int(bool(failed or (args.behaviour and behaviour_failed)))


if __name__ == "__main__":
    sys.exit(main())
