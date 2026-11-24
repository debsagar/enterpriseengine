"""Check that a candidate wheel refuses work persisted by the v0.2.0 wheel."""

from sys import argv
from time import monotonic

from enterpriseengine import Engine


class Desk:
    config = {}

    def handle(self, run, kind, payload):
        assert kind == "review", kind
        run.state.setdefault("reviews", []).append([payload["case"], run.event_started_at, run.now])


def edge(event_id, phase, at):
    return {"id": event_id, "phase": phase, "at": at}


def open_checkpoint():
    return Engine("upgrade-run", Desk(), seed=42, native=None, providers=None, monotonic=monotonic)


def main():
    stage, = argv[1:]
    if stage == "reject":
        from pathlib import Path

        checkpoint = Path("upgrade-run/engine.sqlite3")
        original = checkpoint.read_bytes()
        try:
            open_checkpoint()
        except ValueError as exc:
            assert "requires engine_version >= 0.3.0" in str(exc), exc
        else:
            raise AssertionError("Accepted a v0.2.0 checkpoint")
        assert checkpoint.read_bytes() == original
        print("v0.2.0 checkpoint rejection passed")
        return
    run = open_checkpoint()
    prior_state = {"reviews": [["A", 10, 15]]}
    prior_history = [edge("A", "start", 10), edge("A", "complete", 15), edge("B", "start", 15)]
    if stage == "seed":
        for case, at in (("A", 10), ("B", 15), ("C", 25)):
            run.schedule(case, "review", at, payload={"case": case}, duration=5)
        run.run(until=17)
    assert run.state == prior_state, run.state
    assert run.history() == prior_history, run.history()

    print(f"v0.2.0 checkpoint {stage} passed")


if __name__ == "__main__":
    main()
