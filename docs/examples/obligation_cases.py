"""Run a warehouse shortage or service restoration SLA through employee handoffs."""

import argparse
import json
from time import monotonic

from enterpriseengine import Engine


class CaseDesk:
    """The business handler checks what a role can see before citing a record."""

    resolution_checks = ((18, "verified"), (24, "verified"), (27, "verified"))

    def handle(self, run, kind, payload):
        key = payload["key"]
        role = run.event["role"]
        facts = run.state.setdefault("case_history", [])
        if kind == "inspect":
            facts.append(dict(kind=kind, role=role, at=run.now, obligations=run.obligations()))
            return
        if kind == "obligation_due":
            facts.append(dict(kind=kind, key=key, obligation_id=payload["id"], role=role, at=run.now))
            return
        if kind in ("check", "handoff"):
            obligation_id = run.state.get("case_ids", {}).get(key)
            current = next((row for row in run.obligations() if row["id"] == obligation_id), None)
        if kind == "check":
            attempt = dict(key=key, role=role, at=run.now, event_id=run.event["id"])
            if current is None:
                closed = any(row["kind"] == "resolved" and row["key"] == key
                             and row["role"] == role for row in facts)
                facts.append(dict(kind="already_resolved" if closed else "pending", **attempt))
                return

        record = next((row for row in run.observe(role, as_of=run.now)
                       if row["native_id"] == payload["record"] and row["system"] == self.system
                       and row["case"] == key), None)
        if kind == "handoff":
            if current is None:
                raise PermissionError("Handoff requires a case owned by the active role")
            if record is None or record["kind"] != "eta":
                raise ValueError("Handoff requires a visible ETA for this case")
            due = self.handoff_due(record["eta"], current["due"])
            run.handoff_obligation(obligation_id, owner=self.followup_owner, due=due)
            facts.append(dict(kind="eta", key=key, role=role, at=run.now, eta=record["eta"]))
        elif kind == "open":
            if record is None or record["kind"] != self.opening_kind:
                raise ValueError("Opening requires visible native case evidence")
            self.open_case(run, role, record)
        elif kind == "check":
            confirmed = self.resolution(run, record) if record is not None else None
            if confirmed is None:
                facts.append(dict(kind="pending", **attempt))
                return
            run.resolve_obligation(obligation_id, evidence=self.reference(confirmed))
            facts.append(dict(kind="resolved", native_id=confirmed["native_id"], **attempt))
        else:
            raise ValueError(f"Unknown case action: {kind}")

    def open_case(self, run, role, record):
        confirmed = self.confirm(run, record)
        key = confirmed["case"]
        run.state.setdefault("case_ids", {})[key] = run.open_obligation(
            key, owner=role, due=confirmed["due"], evidence=self.reference(confirmed),
        )
        return confirmed

    @staticmethod
    def reference(record):
        return {"system": record["system"], "native_id": record["native_id"]}

    def confirm(self, run, record):
        fields = {key: value for key, value in record.items() if key not in ("system", "native_id", "kind")}
        return run.confirm(self.system, record["kind"], record["native_id"], fields)

    def fixture_record(self, key, suffix, kind, at, **fields):
        return dict(system=self.system, native_id=f"{key}-{suffix}", case=key, kind=kind, at=at, **fields)

    def native_records(self):
        for key in self.keys:
            yield self.fixture_record(key, "open", self.opening_kind, 10,
                                      roles=[self.initial_owner, self.followup_owner],
                                      **self.opening_fields)
            yield self.fixture_record(key, "eta", "eta", 12, eta=24,
                                      roles=[self.initial_owner, self.followup_owner])
        yield self.fixture_record(self.keys[0], "verified", self.resolution_kind, 24,
                                  roles=[self.followup_owner], **self.resolution_fields)
        yield self.fixture_record(self.keys[1], "partial", self.resolution_kind, 24,
                                  roles=[self.followup_owner], **self.partial_fields)

    def opening(self):
        events = []

        def add(key, kind, at, role, record=None):
            payload = {"key": key}
            if record is not None:
                payload["record"] = f"{key}-{record}"
            events.append(dict(id=f"{kind}:{key}:{at}:{role}", kind=kind, at=at, role=role, payload=payload))

        for key in self.keys:
            add(key, "open", 10, self.initial_owner, "open")
            add(key, "handoff", 12, self.initial_owner, "eta")
            add(key, "check", 17, self.followup_owner, "eta")
        add(self.keys[0], "inspect", 13, self.initial_owner)
        add(self.keys[0], "inspect", 13, self.followup_owner)
        for at, suffix in self.resolution_checks:
            add(self.keys[0], "check", at, self.followup_owner, suffix)
        add(self.keys[1], "check", 24, self.followup_owner, "partial")
        add(self.keys[0], "inspect", 27, self.followup_owner)
        return events


class WarehouseShortage(CaseDesk):
    keys = ("shortage-A", "shortage-B")
    system = "warehouse"
    opening_kind = "pick_shortage"
    scope = ("item", "lot", "unit")
    stock = {"item": "packing-carton", "lot": "CT-42", "unit": "each"}
    opening_fields = {**stock, "units_missing": 12, "due": 20}
    resolution_kind = "receiving_scan"
    resolution_fields = {**stock, "units_accepted": 8, "available_to_pick": True}
    partial_fields = {**stock, "units_accepted": 3, "available_to_pick": True}
    resolution_checks = ((18, "verified"), (24, "verified"), (25, "final"), (27, "final"))
    initial_owner, followup_owner = "picker", "receiving"
    config = {"roles": {role: {} for role in (initial_owner, followup_owner)}}

    def handoff_due(self, eta, current_due):
        return eta + 2

    def open_case(self, run, role, record):
        if (type(record.get("units_missing")) is not int or record["units_missing"] <= 0
                or any(not isinstance(record.get(field), str) or not record[field].strip()
                       for field in self.scope)):
            raise ValueError("A shortage requires a positive unit count and item, lot and unit")
        confirmed = super().open_case(run, role, record)
        run.state.setdefault("warehouse_shortages", {}).setdefault(
            confirmed["case"],
            {**{field: confirmed[field] for field in self.scope},
             "units_missing": confirmed["units_missing"], "receipts": {}},
        )

    def resolution(self, run, record):
        shortage = run.state.get("warehouse_shortages", {}).get(record["case"])
        units = record.get("units_accepted")
        if (shortage is None or record["kind"] != self.resolution_kind
                or any(record.get(field) != shortage[field] for field in self.scope)
                or record.get("available_to_pick") is not True
                or type(units) is not int or units <= 0
                or record["native_id"] in shortage["receipts"]):
            return None

        confirmed = self.confirm(run, record)
        shortage["receipts"][confirmed["native_id"]] = confirmed["units_accepted"]
        if sum(shortage["receipts"].values()) >= shortage["units_missing"]:
            return confirmed
        return None

    def native_records(self):
        yield from super().native_records()
        yield self.fixture_record(self.keys[0], "final", self.resolution_kind, 25,
                                  roles=[self.followup_owner],
                                  **(self.resolution_fields | {"units_accepted": 4}))


class ManagedServicesSLA(CaseDesk):
    keys = ("incident-A", "incident-B")
    system = "service_desk"
    opening_kind = "outage_ticket"
    opening_fields = {"due": 20}
    resolution_kind = "service_probe"
    resolution_fields = {"reachable": True, "transaction_ok": True}
    partial_fields = {"reachable": True, "transaction_ok": False}
    initial_owner, followup_owner = "triage", "on_call"
    config = {"roles": {role: {} for role in (initial_owner, followup_owner)}}

    def handoff_due(self, eta, current_due):
        return current_due

    def resolution(self, run, record):
        if (record["kind"] == self.resolution_kind and record.get("reachable") is True
                and record.get("transaction_ok") is True):
            return self.confirm(run, record)
        return None


class FixtureRecords:
    """Read-only offline transport standing in for independently recorded facts."""

    def __init__(self, records):
        self.rows = list(records)

    def records(self):
        return self.rows

    def read(self, system, native_id):
        return next(row for row in self.rows if (row["system"], row["native_id"]) == (system, native_id))


CASES = {"warehouse": WarehouseShortage, "sla": ManagedServicesSLA}


def open_run(directory, desk, *, boundary=None):
    return Engine(directory, desk, seed=42, native=FixtureRecords(desk.native_records()),
                  providers=None, monotonic=monotonic, boundary=boundary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=CASES)
    parser.add_argument("--run", required=True)
    parser.add_argument("--until", type=int, required=True)
    args = parser.parse_args()
    desk = CASES[args.case]()
    run = open_run(args.run, desk)
    run.extend("opening", events=desk.opening())
    run.run(until=args.until)
    print(json.dumps(run.state.get("case_history", []), indent=2))


if __name__ == "__main__":
    main()
