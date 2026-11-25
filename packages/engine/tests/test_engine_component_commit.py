"""Offline proof that a component commits mid-handler while the event's other edits roll back."""

import unittest
from tempfile import TemporaryDirectory
from time import monotonic

from engine import Engine


class Consumer:
    config = {}
    fail = True

    def handle(self, run, kind, payload):
        run.state["other"]["n"] += 1
        run.state["part"]["steps"].append(run.state["other"]["n"])
        run.commit_component_state("part")
        if self.fail:
            raise RuntimeError("handler stopped")


class ComponentCommit(unittest.TestCase):
    def test_component_survives_failed_event_and_retry_resumes_it(self):
        with TemporaryDirectory() as directory:
            consumer = Consumer()
            run = Engine(directory, consumer, seed=1, native=None, providers=None, monotonic=monotonic)
            run.register_component_state("part", {"steps": []})
            run.register_component_state("other", {"n": 0})
            run.schedule("job", "work", 10, payload={})
            with self.assertRaises(RuntimeError):
                run.run(until=10)
            self.assertEqual(run.state["part"], {"steps": [1]})  # kept
            self.assertEqual(run.state["other"], {"n": 0})  # rolled back with the event
            consumer.fail = False
            run.run(until=10)
            self.assertEqual(run.state["part"], {"steps": [1, 1]})
            self.assertEqual(run.state["other"], {"n": 1})
            reopened = Engine(directory, consumer, seed=1, native=None, providers=None, monotonic=monotonic)
            self.assertEqual(reopened.state["part"], run.state["part"])
            with self.assertRaisesRegex(ValueError, "No state registered"):
                reopened.commit_component_state("missing")


if __name__ == "__main__":
    unittest.main()
