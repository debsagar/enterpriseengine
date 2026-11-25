"""Offline process.tank contracts, including a 90-day reference trajectory."""

import hashlib
import json
import struct
import unittest
from dataclasses import asdict, replace

from components.catalog import manifest, registry
from components.sim import PlantInputs, PlantState, initial_state, step_until_event


def _reference_inputs(n):
    """Deterministic feed/utility and transmitter variation with four control boundaries."""
    return PlantInputs(
        Ff=[15 + (k % 37 - 18) * 0.01 for k in range(n)],
        Tf=[25 + (k % 337 - 168) * 0.01 for k in range(n)],
        Ta=[20 + (k % 841 - 420) * 0.01 for k in range(n)],
        Ph=[6 + (k % 701 - 350) * 0.001 for k in range(n)],
        spT=[78.0 if n // 4 <= k < n // 3 else 75.0 for k in range(n)],
        spL=[2.3 if k >= n // 2 else 2.0 for k in range(n)],
        mT=[0 if n // 5 <= k < n // 4 else 1 for k in range(n)],
        mF=[2] * n,
        mL=[1] * n,
        nT=[(k % 443) * 0.0002 for k in range(n)],
        nF=[(k % 41) * 0.003 for k in range(n)],
        nL=[(k % 59) * -0.0001 for k in range(n)],
    )


class ProcessTankTests(unittest.TestCase):
    def test_stepping_matches_the_original_plant_bit_for_bit_over_90_days(self):
        n = 90 * 24 * 60 * 6
        inputs = _reference_inputs(n)
        state = initial_state(inputs)
        digest = hashlib.sha256()
        encode = struct.Struct("<13d").pack
        changes = []
        while state.k < n:
            start = state.k
            state, rows, event = step_until_event(state, inputs, min(start + 23_011, n), {})
            self.assertEqual(state.k, start + len(rows))
            if event["changes"]:
                changes.append(event["k"])
            state = PlantState(**json.loads(json.dumps(asdict(state), allow_nan=False)))
            digest.update(b"".join(encode(*row[:-1]) for row in rows))
        self.assertEqual(changes, [n // 5, n // 4, n // 3, n // 2])
        # Original sim.py plant(...), transposed to scan-major little-endian float64.
        self.assertEqual(digest.hexdigest(), "2321475d3026a3f96d0e616426535c4b683f5564f06938a2612b9a27b9e76f1b")

    def test_alarm_and_scheduled_changes_share_a_boundary(self):
        inputs = PlantInputs(
            Ff=[15.0] * 4,
            Tf=[25.0] * 4,
            Ta=[20.0] * 4,
            Ph=[6.0] * 4,
            spT=[75.0, 75.0, 76.0, 76.0],
            spL=[2.0] * 4,
            mT=[1, 1, 0, 0],
            mF=[2] * 4,
            mL=[1] * 4,
            nT=[0.0, 0.0, -12.0, 0.0],
            nF=[0.0] * 4,
            nL=[0.0] * 4,
        )
        initial = initial_state(inputs)
        state, rows, event = step_until_event(initial, inputs, 4, {"pvT": 65.0})
        self.assertEqual(
            event, {"kind": "alarm", "k": 2, "alarms": {"pvT": rows[-1].pvT}, "changes": {"spT": 76.0, "mT": 0}}
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual((initial.k, initial.purity_delay), (0, []))
        self.assertEqual((state.k, state.pmT), (3, 0))
        state, rows, event = step_until_event(state, inputs, 4, {"pvT": 65.0})
        self.assertEqual(len(rows), 1)
        self.assertEqual(event, {"kind": "max_steps", "k": 4, "alarms": {}, "changes": {}})
        self.assertEqual(step_until_event(state, inputs, 4, {})[1], [])

    def test_registered_state_and_sampled_analyzer_alarm(self):
        inputs = replace(_reference_inputs(150), nA=[0.0] * 29 + [-4.0, -4.0] + [0.0] * 119)
        initial = initial_state(inputs)
        state, rows, event = step_until_event(initial, inputs, 120, {"AI-101": 96.0})
        self.assertEqual(event["k"], 30)
        self.assertGreaterEqual(rows[29].analyzer, 96.0)
        self.assertLess(rows[30].analyzer, 96.0)
        self.assertEqual(event["alarms"], {"AI-101": rows[30].analyzer})

        tank = {
            "id": "tank",
            "type": "process.tank",
            "config": {},
            "state": asdict(state),
            "parameters": asdict(inputs.parameters),
            "policies": {},
            "relations": {},
        }
        resolved = registry().resolve(manifest([tank]))
        self.assertEqual(resolved["tank"]["descriptor"]["type"], "process.tank")
