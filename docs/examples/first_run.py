"""Run two queued reviews and inspect their actual business minutes."""
import argparse
import json
from pathlib import Path
from time import monotonic

from engine import Engine


class Desk:
    config = {}

    def handle(self, run, kind, payload):
        if kind != "review":
            raise ValueError(f"Unknown work type: {kind}")
        run.state.setdefault("reviews", []).append({
            "case": payload["case"],
            "start": run.event_started_at,
            "finish": run.now,
        })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--opening", type=Path,
                        default=Path(__file__).with_name("first-run") / "opening.json")
    parser.add_argument("--until", type=int, default=560)
    args = parser.parse_args()
    run = Engine(args.run, Desk(), seed=42, native=None, providers=None, monotonic=monotonic)
    opening = json.loads(args.opening.read_text())
    run.extend(opening["id"], roles=opening["roles"], events=opening["events"])
    run.run(until=args.until)
    print(json.dumps(run.state.get("reviews", [])))


if __name__ == "__main__":
    main()
