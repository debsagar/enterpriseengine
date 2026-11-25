"""Check that a candidate wheel resumes work persisted by the v0.2.0 wheel."""

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
    (stage,) = argv[1:]
    run = open_checkpoint()
    prior_state = {"reviews": [["A", 10, 15]]}
    prior_history = [edge("A", "start", 10), edge("A", "complete", 15), edge("B", "start", 15)]
    if stage == "seed":
        for case, at in (("A", 10), ("B", 15), ("C", 25)):
            run.schedule(case, "review", at, payload={"case": case}, duration=5)
        run.run(until=17)
    assert run.state == prior_state, run.state
    assert run.history() == prior_history, run.history()

    if stage == "resume":
        run.run(until=30)
        expected_state = {"reviews": [["A", 10, 15], ["B", 15, 20], ["C", 25, 30]]}
        expected_history = prior_history + [
            edge("B", "complete", 20),
            edge("C", "start", 25),
            edge("C", "complete", 30),
        ]
        run = open_checkpoint()
        run.run(until=40)
        assert run.state == expected_state, run.state
        assert run.history() == expected_history, run.history()
    print(f"v0.2.0 checkpoint {stage} passed")


if __name__ == "__main__":
    main()
