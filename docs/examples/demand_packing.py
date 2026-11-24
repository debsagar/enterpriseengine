"""Use observed weekly sales to book packing work on the engine calendar."""

import argparse
import json
from time import monotonic

from components.weekly_demand import WeeklyDemand
from enterpriseengine import Engine
from engine.world import WEEK


class PackingDesk:
    world_process = WeeklyDemand()
    config = {
        "world": True,
        "world_demand": {"version": 1, "roles": ["planner"]},
    }

    def publish_world_bulletin(self, run, fields):
        """This offline desk has no carrier inbox."""

    def handle(self, run, kind, payload):
        if kind == "plan_packing":
            week = run.now
            readings = run.observe_world(run.event["role"], as_of=week)
            if len(readings) >= 2:
                previous = readings[-2]
                units = previous["pos_units"]
                if units:
                    run.schedule(f"packing:work:{week}", "pack", week, role="packer",
                                 duration=units, payload={"sales_week": previous["at"], "units": units})
            next_week = week + WEEK
            run.schedule(f"packing:plan:{next_week}", "plan_packing", next_week,
                         role="planner", payload={})
        elif kind == "pack":
            run.state.setdefault("packed", []).append({
                **payload, "started_at": run.event_started_at, "completed_at": run.now,
            })
        else:
            raise ValueError(f"Unknown work type: {kind}")


def run_packing(directory, *, seed, until):
    desk = PackingDesk()
    run = Engine(directory, desk, seed=seed, native=None, providers=None, monotonic=monotonic)
    run.extend("packing-opening-v1", roles={
        "planner": {"shifts": [[0, 480]]},
        "packer": {"shifts": [[0, 480]], "staff": 1},
    }, events=[{
        "id": "packing:plan:0", "kind": "plan_packing", "at": 0, "role": "planner", "payload": {},
    }])
    run.run(until=until)
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--until", type=int, default=WEEK + 60)
    args = parser.parse_args()
    run = run_packing(args.run, seed=args.seed, until=args.until)
    print(json.dumps(run.state.get("packed", [])))


if __name__ == "__main__":
    main()
