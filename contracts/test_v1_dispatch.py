"""Offline entity dispatch contract for an invented observatory desk."""

from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
import unittest

from engine import Engine


class Observatory:
    config = {}

    def handle(self, run, kind, payload):
        raise AssertionError(f"Targeted event reached the legacy handler: {kind}")


def review_frame(run, entity, spec, kind, payload):
    assert kind == "review"
    assert run.event["entity"] == entity
    role = run.event["role"]
    visible = run.observe(role, as_of=run.now)
    if payload.get("write"):
        run.write("plates", "review", {"frame": payload["frame"]})
    run.state.setdefault(entity, []).append(
        [spec["station"], payload["frame"], [row["native_id"] for row in visible]]
    )


class Plates:
    def __init__(self):
        self.sent = {}
        self.sends = self.reads = 0

    def records(self):
        return [
            {"system": "plates", "native_id": "visible", "at": 4, "roles": ["reviewer"]},
            {"system": "plates", "native_id": "future", "at": 6, "roles": ["reviewer"]},
            {"system": "plates", "native_id": "private", "at": 4, "roles": ["director"]},
            {"system": "office", "native_id": "other-system", "at": 4},
        ]

    def send(self, system, key, fields):
        self.sends += 1
        self.sent[key] = fields
        return key

    def find(self, system, key):
        return key if key in self.sent else None

    def read(self, system, key):
        self.reads += 1
        return self.sent[key]


class DispatchTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(dir=".")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.native = Plates()
        self.run = self.reopen()
        self.run.extend("roles", roles={"reviewer": {
            "shifts": [[0, 60]], "permissions": ["observe:plates", "write:plates:review"],
        }})
        self.entities = {"north": {"type": "frame-review@1", "station": "North"}}

    def reopen(self, boundary=None, *, handlers=None):
        return Engine(
            self.directory, Observatory(), seed=17, native=self.native, providers=None,
            monotonic=monotonic, boundary=boundary,
            handlers={"frame-review@1": review_frame} if handlers is None else handlers,
        )

    def event(self, frame, *, entity="north", **payload):
        return dict(id=frame, entity=entity, kind="review", at=5, duration=1,
                    role="reviewer", payload={"frame": frame, **payload})

    def test_identical_replay_applies_once_and_ties_survive_restart(self):
        events = [self.event("Z"), self.event("A")]
        self.run.extend_entities("opening", entities=self.entities, events=events)
        self.run.run(until=5)
        resumed = self.reopen()
        resumed.run(until=6)
        resumed.extend_entities("opening", entities=self.entities, events=events)
        resumed.run(until=7)
        self.assertEqual([row[1] for row in resumed.state["north"]], ["Z", "A"])
        self.assertEqual([row["id"] for row in resumed.history() if row["phase"] == "complete"],
                         ["Z", "A"])

    def test_changed_replay_and_invalid_batch_leave_no_partial_work(self):
        original = self.event("Z")
        self.run.extend_entities("opening", entities=self.entities, events=[original])
        south = {"south": {"type": "frame-review@1", "station": "South"}}
        with self.assertRaisesRegex(ValueError, "Conflicting extension ID"):
            self.run.extend_entities("opening", entities=south, events=[self.event("A", entity="south")])
        with self.assertRaisesRegex(ValueError, "Unknown entity ID"):
            self.run.extend_entities("new", entities=south, events=[
                self.event("A", entity="south"), self.event("B", entity="missing"),
            ])
        self.assertFalse(self.run.has_event("A"))
        resumed = self.reopen()
        self.assertFalse(resumed.has_event("A"))
        resumed.extend_entities("new", entities=south, events=[self.event("A", entity="south")])
        resumed.run(until=6)
        self.assertEqual(resumed.state["south"][0][:2], ["South", "A"])

    def test_visibility_at_dispatch(self):
        event = self.event("Z")
        event["duration"] = 0
        self.run.extend_entities("opening", entities=self.entities, events=[event])
        self.run.run(until=5)
        self.assertEqual(self.run.state["north"], [["North", "Z", ["visible"]]])

    def test_scheduled_work_uses_the_same_entity_after_restart(self):
        self.run.extend_entities("opening", entities=self.entities, events=[])
        event = self.event("Z")
        self.run.schedule(**event)
        self.reopen().run(until=6)
        self.assertEqual(self.reopen().state["north"][0][:2], ["North", "Z"])

    def test_missing_handler_fails_and_preserves_work(self):
        self.run.extend_entities("opening", entities=self.entities, events=[self.event("Z")])
        with self.assertRaisesRegex(RuntimeError, "No registered handler"):
            self.reopen(handlers={}).run(until=6)
        resumed = self.reopen()
        resumed.run(until=6)
        self.assertEqual([row[1] for row in resumed.state["north"]], ["Z"])

    def test_interrupted_native_write_confirms_once(self):
        def interrupt(stage, operation):
            if stage == "after_send":
                raise InterruptedError("simulated loss between send and readback")

        self.run.extend_entities("opening", entities=self.entities, events=[self.event("Z", write=True)])
        with self.assertRaises(InterruptedError):
            self.reopen(interrupt).run(until=6)
        resumed = self.reopen()
        self.assertEqual(resumed.export()["records"], [])
        resumed.run(until=6)
        self.reopen().run(until=7)
        self.assertEqual((self.native.sends, self.native.reads), (1, 1))
        self.assertEqual([record["frame"] for record in resumed.export()["records"]], ["Z"])
        self.assertEqual([row[1] for row in resumed.state["north"]], ["Z"])
