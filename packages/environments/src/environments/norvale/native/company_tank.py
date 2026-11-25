"""TK-101 inside the Norvale company loop, offline.

The plant itself lives in `environments.consumers.tank`. This module adds what a company does
around it: a named role's deterministic pause/continue decision, the fixed records written into
offline QMS, ERP and mail doubles (each carrying the engine event that caused it), and the
doubles' snapshot for `Engine.fork()`. Nothing here calls a model or a live service.
"""

import datetime
import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from engine import DAY

from environments.consumers import tank
from environments.norvale.native.company_erp import CompanyERP
from environments.norvale.walkthrough import ROLES

FILE = "tank-externals.json"
SYSTEMS = {"qms": "QMS", "erp": "ERP", "mail": "MAIL"}
EPOCH = datetime.date(2027, 1, 1)


class Double:
    """One offline application, stored by retry key so a replayed write finds its record."""

    def __init__(self, system, prefix, rows, save):
        self.system, self.prefix, self.rows, self.save = system, prefix, rows, save

    def find(self, system, key):
        return self.rows.get(key, {}).get("native_id")

    def send(self, system, key, document):
        if key not in self.rows:
            native_id = f"{self.prefix}-{len(self.rows) + 1:03d}"
            self.rows[key] = {**document, "system": system, "native_id": native_id}
            self.save()
        return self.rows[key]["native_id"]

    def read(self, system, native_id):
        return deepcopy(next(row for row in self.rows.values() if row["native_id"] == native_id))

    def records(self):
        return deepcopy(list(self.rows.values()))


class TankExternals:
    """The offline QMS, ERP and mail doubles for TK-101, kept in the engine directory.

    They are the run's attached external systems, so `snapshot` is what `Engine.fork()` needs
    to give a branch its own copy.
    """

    def __init__(self, directory):
        self.path = Path(directory) / FILE
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {name: {} for name in SYSTEMS}
        self.doubles = {name: Double(name, SYSTEMS[name], self.data[name], self.save) for name in SYSTEMS}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, sort_keys=True))
        temporary.replace(self.path)

    def bind(self, transport):
        for name, double in self.doubles.items():
            transport.bind(name, name, double)

    def snapshot(self, staging, minute):
        late = [row["at"] for rows in self.data.values() for row in rows.values() if row["at"] > minute]
        if late:
            raise RuntimeError(f"Offline doubles hold records after the fork minute {minute}")
        Path(staging, FILE).write_text(json.dumps(self.data, sort_keys=True))


def date(clock):
    return (EPOCH + timedelta(days=clock.now // DAY)).isoformat()


def email(role):
    roster = {r["role_id"]: r["email"].replace(".example", ".test") for r in ROLES}
    return roster[role]


def register(company, clock):
    """Setup (A): add process.tank to the company's engine and note it in the company state."""
    cfg = company.config["tank"]
    for required in ("role", "policies", "orders", "lots"):
        if required not in cfg:
            raise ValueError(f"tank config requires {required}")
    tank.register(clock)
    company.state["tank"] = {"component": "process.tank", "registered_at": clock.state[tank.COMPONENT]["origin"]}


def decide(company, clock, policy):
    """The named role applies a configured policy to what it can see; the decision keeps its inputs."""
    cfg = clock.config["tank"]
    state = clock.state[tank.COMPONENT]
    if state["alarm"] is None or state["operator"] is not None:
        raise ValueError("Operator decision needs an open alarm and no earlier action")
    rule = cfg["policies"][policy]
    alarms = state["alarm"]["alarms"]
    action = next((rule["on_alarm"][tag] for tag in sorted(alarms) if tag in rule["on_alarm"]), rule["default"])
    key = f"tank-operator:{state['alarm']['k']}"
    evidence = {
        "alarm": alarms,
        "alarm_scan": state["alarm"]["k"],
        "detected_at": state["alarm"]["detected_at"],
        "analyzer_AI_101": state["plant"]["analyzer"],
        "temperature_T": state["plant"]["T"],
        "policy": policy,
        "rule": rule,
    }
    reason = f"Policy {policy} maps alarm {sorted(alarms)} to {action}."
    company.state["decisions"][key] = {
        "mode": "offline-operating-rule",
        "input": {
            "role": cfg["role"],
            "as_of": date(clock),
            "evidence": evidence,
            "question": "Pause or continue feed to TK-101 after the alarm?",
        },
        "output": {"action": action, "reason": reason, "message": reason},
        "event": clock.event["id"],
    }
    return action


class Authority:
    """CompanyERP's lot authority: disposition from the confirmed QMS inspection record."""

    def __init__(self, inspections):
        self.inspections = inspections

    def disposition(self, lot, *, as_of, role):
        inspection = self.inspections[lot["batch"]]
        ok = inspection["result"] == "pass"
        return {
            "batch": lot["batch"],
            "item": lot["item"],
            "uom": lot["uom"],
            "id": inspection["native_id"],
            "version": "inspection-1",
            "status": "released" if ok else "hold",
            "qty": lot["qty"] if ok else 0,
            "authorized": True,
            "approver": "QA_TECH",
            "evidence": f"AI-101 {inspection['reading']}",
        }


def erp(company, clock):
    """The company's own CompanyERP over the tank lots, kept apart from the ERPNext-backed one."""
    host = SimpleNamespace(state=company.state.setdefault("tank_erp", {}), date=lambda: date(clock))
    inspections = company.state.setdefault("tank_inspections", {})
    return CompanyERP(host, Authority(inspections))


def lot_records(company, clock, batch):
    """Write the fixed records for one inspected lot; each names the engine event that caused it."""
    cfg = clock.config["tank"]
    lot = clock.state[tank.COMPONENT]["lots"][batch]
    event = clock.event["id"]
    passed = lot["reading"] >= lot["spec_min"]
    inspection = clock.write(
        "qms",
        "inspection",
        {
            "engine": event,
            "batch": batch,
            "item": lot["item"],
            "qty": lot["qty"],
            "analyzer": "AI-101",
            "reading": lot["reading"],
            "spec_min": lot["spec_min"],
            "result": "pass" if passed else "fail",
        },
    )
    company.state.setdefault("tank_inspections", {})[batch] = inspection

    ledger = erp(company, clock)
    ledger.state["lots"][batch] = {
        "batch": batch,
        "item": lot["item"],
        "kind": "finished",
        "qty": lot["qty"],
        "uom": "Nos",
    }
    disposition = ledger.disposition(ledger.state["lots"][batch], "QA")
    ledger.state.setdefault("synced", {})[batch] = disposition
    release = clock.write(
        "erp",
        "batch_release",
        {
            "engine": event,
            "batch": batch,
            "item": lot["item"],
            "status": disposition["status"],
            "released_qty": disposition["qty"],
            "disposition": disposition["id"],
        },
    )
    supply = ledger.finished_supply()
    for order, wanted in sorted(cfg["orders"].items()):
        clock.write(
            "erp",
            "shortfall",
            {
                "engine": event,
                "batch": batch,
                "order": order,
                "item": wanted["item"],
                "ordered": wanted["qty"],
                "stock": supply.get(wanted["item"], 0),
                "shortfall": max(0, wanted["qty"] - supply.get(wanted["item"], 0)),
            },
        )
    if batch == cfg["lots"][-1]["batch"]:
        operator = clock.state[tank.COMPONENT]["operator"]
        decided = operator["action"] if operator else "no action"
        clock.write(
            "mail",
            "message",
            {
                "engine": event,
                "from": email("SHIFT_OP"),
                "to": email("PLANT_MGR"),
                "subject": f"TK-101 lot {batch} {release['status']}",
                "body": f"Shift handover: operator action was {decided}; AI-101 read {lot['reading']} against "
                f"{lot['spec_min']} for lot {batch}, so it is {release['status']}.",
            },
        )


def handle(company, clock, kind, payload):
    """Dispatch one tank event for the company: plant steps, role decision and fixed records."""
    if kind == "tank_operator" and "policy" in payload:
        action = decide(company, clock, payload["policy"])
        return tank.handle(clock, kind, {"action": action})
    tank.handle(clock, kind, payload)
    if kind == "tank_lot":
        lot_records(company, clock, payload["batch"])


def inject_policy(clock, policy):
    """After a fork: hand the configured role one named policy, as that role's next event."""
    cfg = clock.config["tank"]
    if policy not in cfg["policies"]:
        raise ValueError(f"Unknown tank policy {policy!r}")
    clock.schedule(
        f"tank-policy-{policy}", "tank_operator", clock.now + 1, role=cfg["role"], payload={"policy": policy}
    )


def snapshot_external(company, staging, minute):
    """Engine.fork hook: copy every attached external store, or refuse the ones we cannot copy."""
    if getattr(company, "eps", None):
        raise RuntimeError(
            "Live application stacks (ERPNext, Gitea, QMS, mail) are attached and have no snapshot; "
            "fork a run whose external systems are the offline doubles"
        )
    company.tank_externals.snapshot(staging, minute)
