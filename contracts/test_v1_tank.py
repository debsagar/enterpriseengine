"""Offline TK-101 compatibility and checkpoint contracts."""

from dataclasses import asdict, replace
import json
from math import isclose
from pathlib import Path
import unittest

from components.equipment.tank import (
    DESCRIPTOR, ScanInput, TankState, initial_state, step_minute, step_scan,
)
from components.registry import Registry, VERSION


FIXTURE = Path(__file__).with_name("fixtures") / "tk101_v1.json"


class TankContract(unittest.TestCase):
    def check_trace(self, name):
        fixture = json.loads(FIXTURE.read_text())
        case = fixture["cases"][name]
        scans = [ScanInput(**(fixture["input_defaults"] | row)) for row in case["overrides"]]
        state = initial_state(scans[0])
        self.assertEqual(len(case["expected_by_scan"]), len(scans))
        self.assertEqual(len(fixture["outputs"]), 13)
        for start in range(0, len(scans), 6):
            state, observed, truth = step_minute(state, scans[start:start + 6])
            self.assertEqual(len(observed), 6)
            self.assertEqual(len(truth), 6)
            for index, (sample, private) in enumerate(zip(observed, truth), start):
                self.assertEqual(sample["at_seconds"], (index + 1) * 10)
                self.assertEqual(sample["at_seconds"], private["at_seconds"])
                self.assertEqual(set(sample) & set(private), {"at_seconds"})
                row = sample | private
                expected = case["expected_by_scan"][index]
                self.assertEqual(len(expected), 13)
                for field, target in zip(fixture["outputs"], expected):
                    with self.subTest(scan=index, output=field):
                        # Double precision and libm rounding across platforms.
                        self.assertTrue(isclose(row[field], target, rel_tol=1e-12, abs_tol=1e-9),
                                        f"{row[field]} != {target}")

    def test_nominal(self):
        self.check_trace("nominal")

    def test_pressure_step(self):
        self.check_trace("pressure_step")

    def test_resume_during_manual_mode(self):
        nominal = ScanInput()
        upset = replace(nominal, pressure_barg=5.25, temp_sp=78.0, flow_offset=12.0)
        manual = replace(upset, temp_mode=0, flow_mode=0, level_mode=0, feed_m3_h=16.5)
        scans = [nominal, upset, manual, manual, manual, upset]
        opening = initial_state(nominal, at_seconds=600)
        final, observed, truth = step_minute(opening, scans)
        self.assertEqual(opening.at_seconds, 600)
        self.assertEqual(final.at_seconds, 660)
        self.assertEqual([sample["TIC-101.MODE"] for sample in observed], [1, 1, 0, 0, 0, 1])
        for tag in ("TIC-101.OP", "FIC-201.OP", "LIC-101.OP"):
            self.assertEqual(observed[3][tag], observed[1][tag])

        state = opening
        for inputs in scans[:4]:
            state, _, _ = step_scan(state, inputs)
        payload = json.loads(json.dumps(asdict(state), allow_nan=False))
        resolved = Registry([DESCRIPTOR]).resolve({
            "version": VERSION, "adapters": [],
            "instances": [{
                "id": "TK-101", "type": DESCRIPTOR["type"], "config": {},
                "state": payload, "parameters": {}, "policies": {}, "relations": {},
            }],
        })
        state = TankState(**resolved["TK-101"]["instance"]["state"])
        for index in range(4, 6):
            state, sample, private = step_scan(state, scans[index])
            self.assertEqual(sample, observed[index])
            self.assertEqual(private, truth[index])
        self.assertEqual(state, final)

    def test_minute_requires_six_scans(self):
        inputs = ScanInput()
        state = initial_state(inputs)
        for count in (5, 7):
            with self.subTest(count=count), self.assertRaises(ValueError):
                step_minute(state, [inputs] * count)
        self.assertEqual(state.at_seconds, 0)
