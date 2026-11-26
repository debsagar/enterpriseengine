"""TK-101 heated tank on the engine clock: plant state in the checkpoint, alarms as events.

The plant's checkpoint (components.sim.PlantState) is registered with the engine, so
Engine.fork() copies it with the queue. Six 10-second scans make one business minute. Config minutes count from the
minute the plant was registered, which is its scan 0.
"""

from dataclasses import asdict

from components.sim import PlantInputs, PlantState, initial_state, step_until_event

COMPONENT = "process_tank"
KINDS = ("tank_run", "tank_boundary", "tank_operator", "tank_lot")
SCANS = 6


class _Step:
    """`n` scans reading `before` up to scan `at` and `after` from it on, without storing them."""

    def __init__(self, n, before, at=None, after=None):
        self.n, self.before, self.after = n, before, after
        self.at = n if at is None else at

    def __len__(self):
        return self.n

    def __getitem__(self, k):
        if not 0 <= k < self.n:
            raise IndexError(k)
        return self.before if k < self.at else self.after


def inputs(cfg, operator):
    """Rebuild the scan timeline from config and the recorded operator action."""
    n = cfg["horizon_minutes"] * SCANS
    drop = cfg["steam_drop"]["at_minute"] * SCANS
    feed = cfg["feed"]
    cut = operator["k"] if operator and operator["action"] == "pause" else n
    zero = _Step(n, 0.0)
    return PlantInputs(
        Ff=_Step(n, feed, cut, feed * cfg["pause_feed_scale"]),
        Tf=_Step(n, 25.0),
        Ta=_Step(n, 20.0),
        Ph=_Step(n, 6.0, drop, cfg["steam_drop"]["pressure"]),
        spT=_Step(n, 78.0),
        spL=_Step(n, 2.0),
        mT=_Step(n, 1),
        mF=_Step(n, 2),
        mL=_Step(n, 1),
        nT=zero,
        nF=zero,
        nL=zero,
        nA=zero,
    )


def register(engine):
    """Add the plant to a fresh engine and schedule its first scan chunk."""
    cfg = engine.config["tank"]
    if COMPONENT in engine.state:
        return
    state = initial_state(inputs(cfg, None))
    engine.register_component_state(
        COMPONENT,
        {"origin": engine.now, "plant": asdict(state), "alarm": None, "operator": None, "boundaries": [], "lots": {}},
    )
    engine.schedule("tank-run-0", "tank_run", engine.now, payload={})
    for lot in cfg["lots"]:
        engine.schedule(f"tank-lot-{lot['batch']}", "tank_lot", engine.now + lot["at_minute"], payload=lot)


def _scan(engine):
    return (engine.now - engine.state[COMPONENT]["origin"]) * SCANS


def _advance(engine):
    """Step the plant to the clock; an alarm becomes a tank_boundary event at this minute."""
    tank, cfg = engine.state[COMPONENT], engine.config["tank"]
    timeline = inputs(cfg, tank["operator"])
    plant = PlantState(**tank["plant"])
    target = min(_scan(engine), cfg["horizon_minutes"] * SCANS)
    while plant.k < target:
        limits = {} if tank["alarm"] else cfg["alarms"]
        plant, _, event = step_until_event(plant, timeline, target, limits)
        if event["kind"] == "alarm":
            tank["alarm"] = {"k": event["k"], "alarms": event["alarms"], "detected_at": engine.now}
            engine.schedule(f"tank-boundary-{event['k']}", "tank_boundary", engine.now, payload=tank["alarm"])
    tank["plant"] = asdict(plant)
    return plant


def handle(engine, kind, payload):
    tank, cfg = engine.state[COMPONENT], engine.config["tank"]
    if kind == "tank_run":
        _advance(engine)
        at = engine.now + cfg["chunk_minutes"]
        if _scan(engine) + cfg["chunk_minutes"] * SCANS <= cfg["horizon_minutes"] * SCANS:
            engine.schedule(f"tank-run-{at}", "tank_run", at, payload={})
    elif kind == "tank_boundary":
        tank["boundaries"].append(payload)
    elif kind == "tank_operator":
        if payload["action"] not in ("pause", "continue"):
            raise ValueError("Operator action must be pause or continue")
        tank["operator"] = {"action": payload["action"], "k": _scan(engine)}
    else:  # tank_lot: an inspection reads the AI-101 analyzer as of this minute
        plant = _advance(engine)
        tank["lots"][payload["batch"]] = {
            "item": payload["item"],
            "qty": payload["qty"],
            "at": engine.now,
            "reading": round(plant.analyzer, 4),
            "spec_min": cfg["alarms"]["AI-101"],
        }
