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


def inputs(cfg, operator):
    """Rebuild the scan timeline from config and the recorded operator action."""
    n = cfg["horizon_minutes"] * SCANS
    drop = cfg["steam_drop"]["at_minute"] * SCANS
    feed = cfg["feed"]
    cut = operator["k"] if operator and operator["action"] == "pause" else n
    ph_initial = cfg.get("Ph", 6.0)
    return PlantInputs(
        Ff=[feed * cfg["pause_feed_scale"] if k >= cut else feed for k in range(n)],
        Tf=[cfg.get("Tf", 25.0)] * n,
        Ta=[cfg.get("Ta", 20.0)] * n,
        Ph=[cfg["steam_drop"]["pressure"] if k >= drop else ph_initial for k in range(n)],
        spT=[cfg.get("spT", 78.0)] * n,
        spL=[cfg.get("spL", 2.0)] * n,
        mT=[cfg.get("mT", 1)] * n,
        mF=[cfg.get("mF", 2)] * n,
        mL=[cfg.get("mL", 1)] * n,
        nT=[cfg.get("nT", 0.0)] * n,
        nF=[cfg.get("nF", 0.0)] * n,
        nL=[cfg.get("nL", 0.0)] * n,
        nA=[cfg.get("nA", 0.0)] * n,
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
