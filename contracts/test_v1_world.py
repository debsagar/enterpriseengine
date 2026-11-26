"""Demand opt-in, legacy compatibility and the packing consumer's calendar."""

import json
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from unittest import TestCase
from unittest.mock import Mock, patch

from components import weekly_demand
from engine import world
from enterpriseengine import Engine

from docs.examples.demand_packing import PackingDesk, run_packing


class WorldDesk:
    def __init__(self, config):
        self.config = config

    def publish_world_bulletin(self, run, fields):
        run.state.setdefault("bulletins", []).append({"at": run.now, **fields})

    def delivery_notice(self, run, fields, eta):
        run.state.setdefault("notices", []).append({"at": run.now, "eta": eta, **fields})

    def delivery_arrival(self, run, fields):
        run.state.setdefault("receipts", []).append({"at": run.now, **fields})

    def handle(self, run, kind, payload):
        world.dispatch(run, payload["key"], payload["eta"], {"units": 30})


def engine(directory, desk, seed=42, start=0):
    return Engine(directory, desk, seed=seed, native=None, providers=None, monotonic=monotonic, start=start)


def legacy_results(directory, seed, config):
    start = 17
    desk = WorldDesk(config)
    if config.get("world_demand"):
        desk.world_process = weekly_demand.WeeklyDemand()
    run = engine(directory, desk, seed, start)
    for week in (0, 3, 7):
        eta = start + week * world.WEEK + 1
        run.schedule(f"shipment:{week}", "dispatch", eta - 1, payload={"key": str(week), "eta": eta})
    run.run(until=start + 4 * world.WEEK)
    run = engine(directory, desk, seed, start)
    run.run(until=start + 16 * world.WEEK)
    truth = run.private_truth()
    # The goldens predate the answer-key records added for P0; they cover the legacy keys.
    for key in ("entities", "links", "cases", "decisions"):
        truth.pop(key)
    for row in truth["world"]:
        row.pop("process", None)
    return [
        truth,
        {key: value for key, value in run.state.items() if not key.startswith("_")},
        run.history(),
        run.export(),
    ]


class DemandFactor(TestCase):
    def test_legacy_world_seed_results_and_ids(self):
        # Golden digests captured using reviewed dev 341952e, including delivery
        # timing, bulletins, private samples and the complete calendar history.
        goldens = {
            1: "ae4c55cebf3a2937abe82ca82fc6a4cf78787c9cde664e81a1ba0166cf86178e",
            42: "7f7280e475dcdd4002c48010965b784a30cf74f4321f49516cc93cd2a81112a8",
            993: "efaf917a8201399093d1f7edea3f61f6e9a6f12ed1ce8c3205fa4726913fedfa",
        }
        with TemporaryDirectory() as root:
            for seed, digest in goldens.items():
                for enabled in (False, True):
                    with self.subTest(seed=seed, enabled=enabled):
                        config = {"world": True, "world_fixture": {"initial_disruption": "short_onset"}}
                        if enabled:
                            config["world_demand"] = PackingDesk.config["world_demand"]
                        results = legacy_results(Path(root) / f"{seed}-{enabled}", seed, config)
                        actual = sha256(json.dumps(results, sort_keys=True).encode()).hexdigest()
                        self.assertEqual(actual, digest)

    def test_durations_shared_onset_and_separate_noise(self):
        cases = (
            (None, 0.044, "normal", 0, 20),
            (("normal", 0), 0.029, "promo_spike", 0, 26),
            (("normal", 0), 0.044, "seasonal_lift", 0, 26),
            (("normal", 0), 0.049, "structural_decline", 0, 14),
            (("normal", 0), 0.051, "normal", 1, 20),
            (("promo_spike", 2), 0.99, "promo_spike", 3, 26),
            (("promo_spike", 3), 0.01, "normal", 0, 20),
            (("seasonal_lift", 0), 0.84, "seasonal_lift", 1, 30),
            (("seasonal_lift", 0), 0.86, "normal", 0, 20),
            (("seasonal_lift", 6), 0.01, "seasonal_lift", 7, 30),
            (("seasonal_lift", 7), 0.01, "normal", 0, 20),
            (("structural_decline", 17), 0.96, "structural_decline", 18, 14),
            (("structural_decline", 17), 0.98, "normal", 0, 20),
        )
        for prior, coin, regime, age, mean in cases:
            with self.subTest(prior=prior, coin=coin):
                previous = dict(zip(("regime", "age"), prior)) if prior else None
                with patch("components.weekly_demand.stream") as stream:
                    rngs = {
                        key: Mock()
                        for key in (
                            "demand-v1-regime",
                            "demand-v1-realized",
                            "demand-v1-forecast",
                        )
                    }
                    stream.side_effect = lambda seed, key, at: rngs[key]
                    rngs["demand-v1-regime"].random.return_value = coin
                    rngs["demand-v1-realized"].gauss.return_value = -3.4
                    rngs["demand-v1-forecast"].gauss.return_value = 20.8
                    sample = weekly_demand.demand_v1(previous, 42, 17)
                    self.assertEqual(sample, dict(regime=regime, age=age, mean=mean, pos_units=0, demand_forecast=21))
                    rngs["demand-v1-realized"].gauss.assert_called_once_with(mean, 4)
                    rngs["demand-v1-forecast"].gauss.assert_called_once_with(mean, 6)
                    for call in stream.call_args_list:
                        self.assertEqual((call.args[0], call.args[2]), (42, 17))

    def test_opt_in_validation_and_reserved_event_collisions(self):
        with TemporaryDirectory() as root:
            for world_enabled, option in (
                (False, {"version": 1, "roles": ["planner"]}),
                (True, None),
                (True, {"version": True, "roles": ["planner"]}),
                (True, {"version": 2, "roles": ["planner"]}),
                (True, {"version": 1, "roles": []}),
                (True, {"version": 1, "roles": ["planner", "planner"]}),
                (True, {"version": 1, "roles": [""]}),
                (True, {"version": 1, "roles": [["planner"]]}),
                (True, {"version": 1, "roles": "planner"}),
                (True, {"version": 1, "roles": ["planner"], "extra": 1}),
            ):
                with self.subTest(option=option), self.assertRaisesRegex(ValueError, "world_demand|world: true"):
                    desk = WorldDesk({"world": world_enabled, "world_demand": option})
                    desk.world_process = weekly_demand.WeeklyDemand()
                    engine(root, desk)
            run = engine(root, PackingDesk())
            for event_id, kind in (("world:week:0", "noop"), ("world:week:1", "noop"), ("packing:plan:0", world.TICK)):
                with self.subTest(event_id=event_id, kind=kind):
                    with self.assertRaisesRegex(ValueError, "Reserved world event"):
                        run.schedule(event_id, kind, 0, payload={})
                    with self.assertRaisesRegex(ValueError, "Reserved world event"):
                        run.extend(
                            "extension",
                            events=[
                                dict(id=event_id, kind=kind, at=0, payload={}),
                            ],
                        )
            run.extend("extension", events=[dict(id="demand:0", kind="noop", at=0, payload={})])
            run.run(until=0)
            self.assertEqual(len(run.observe_world("planner", as_of=0)), 1)
            self.assertEqual(len(run.private_truth()["world"]), 1)

    def test_reopen_and_packing_work_follow_realized_units(self):
        with TemporaryDirectory() as root:
            root = Path(root)
            until = 10 * world.WEEK + 60
            continuous = run_packing(root / "continuous", seed=42, until=until)
            first = run_packing(root / "resumed", seed=42, until=0)
            self.assertEqual(first.state.get("packed", []), [])
            self.assertFalse(first.has_event("packing:work:0"))
            self.assertIsNone(first.observe_world("planner", as_of=0)[0]["pos_units"])
            for config, seed in (
                (PackingDesk.config, 43),
                ({"world": True, "world_demand": {"version": 1, "roles": ["packer"]}}, 42),
            ):
                desk = PackingDesk()
                desk.config = config
                with self.assertRaisesRegex(ValueError, "original seed and configuration"):
                    engine(root / "resumed", desk, seed)
            resumed = run_packing(root / "resumed", seed=42, until=until)
            self.assertEqual(resumed.private_truth(), continuous.private_truth())
            self.assertEqual(resumed.history(), continuous.history())
            self.assertEqual(resumed.state["packed"], continuous.state["packed"])
            readings = resumed.observe_world("planner", as_of=until)
            self.assertEqual(len(readings), 11)
            self.assertTrue(any(row["pos_units"] != row["demand_forecast"] for row in readings[:-1]))
            self.assertGreater(len({row["pos_units"] for row in readings[:-1]}), 1)
            self.assertEqual(
                resumed.state["packed"],
                [
                    {
                        "sales_week": row["at"],
                        "units": row["pos_units"],
                        "started_at": row["at"] + world.WEEK,
                        "completed_at": row["at"] + world.WEEK + row["pos_units"],
                    }
                    for row in readings[:-1]
                    if row["pos_units"]
                ],
            )

    def test_observation_privacy_and_no_uncommitted_sample(self):
        class Probe(WorldDesk):
            world_process = weekly_demand.WeeklyDemand()
            interrupt = True

            def publish_world_bulletin(self, run, fields):
                if self.interrupt:
                    raise RuntimeError("interrupted bulletin")
                super().publish_world_bulletin(run, fields)

            def handle(self, run, kind, payload):
                role = run.event["role"]
                with self.test.assertRaisesRegex(ValueError, "active event role"):
                    run.observe_world("planner" if role == "sales" else "sales", as_of=run.now)
                with self.test.assertRaisesRegex(ValueError, "future"):
                    run.observe_world(role, as_of=run.now + 1)
                with self.test.assertRaises(PermissionError):
                    run.private_truth()
                self.test.assertNotIn("pos_units", json.dumps(run.state["_world"]))
                run.state[role] = run.observe_world(role, as_of=run.now)

        with TemporaryDirectory() as root:
            probe = Probe(PackingDesk.config)
            probe.test = self
            run = engine(root, probe, start=17)
            run.schedule("probe:planner", "probe", 17, role="planner", payload={})
            run.schedule("probe:sales", "probe", 17, role="sales", payload={})
            with self.assertRaisesRegex(RuntimeError, "interrupted bulletin"):
                run.run(until=17)
            self.assertEqual(run.observe_world("planner", as_of=17), [])
            self.assertEqual(run.private_truth()["world"], [])
            probe.interrupt = False
            run = engine(root, probe)
            run.run(until=17 + world.WEEK)
            self.assertEqual(run.state["sales"], [])
            self.assertEqual(run.observe_world("planner", as_of=16), [])
            obs = run.state["planner"]
            self.assertEqual(obs, run.observe_world("planner", as_of=17))
            self.assertEqual(len(obs), 1)
            self.assertEqual(set(obs[0]), {"at", "pos_units", "demand_forecast"})
            obs[0]["pos_units"] = -1
            self.assertIsNone(run.observe_world("planner", as_of=17)[0]["pos_units"])
            self.assertIsNone(run.observe_world("planner", as_of=16 + world.WEEK)[0]["pos_units"])
            released = run.observe_world("planner", as_of=17 + world.WEEK)
            self.assertGreaterEqual(released[0]["pos_units"], 0)
            self.assertIsNone(released[1]["pos_units"])
            self.assertEqual(run.observe_world("sales", as_of=run.now), [])
            self.assertEqual(run.observe_world(None, as_of=run.now), [])
            self.assertEqual(run.private_truth(as_of=17)["world"][0]["process"]["regime"], "normal")
            self.assertEqual(len(run.private_truth(as_of=17)["world"]), 1)
            self.assertNotIn("demand", json.dumps(run.export()))
            self.assertNotIn("demand", json.dumps(run.trace()))
            self.assertNotIn("demand_forecast", run.viewer())
            self.assertIn("demand_forecast", run.viewer(include_private=True))

    def test_legacy_and_world_disabled_have_no_demand(self):
        with TemporaryDirectory() as root:
            for enabled in (False, True):
                run = engine(Path(root) / str(enabled), WorldDesk({"world": enabled}))
                run.run(until=world.WEEK)
                self.assertEqual(run.observe_world("planner", as_of=world.WEEK), [])
                self.assertNotIn("demand", json.dumps(run.private_truth()))
