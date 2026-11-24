"""TK-101 process and control, ported from /data/rca-eval/base/sim.py:plant.

Physical and controller constants come from sim.py:P (process description
§2–5). The three transmitter lags come from base/tags_explained.csv.
The deterministic kernel takes calibration offsets as inputs. Disturbance
generation, historian noise, quantization, and compression are upstream or
downstream concerns, so this component has no random stream.
"""

from dataclasses import dataclass, replace
from math import exp, log, pi, sqrt

from ..registry import VERSION

SCAN_SECONDS = 10
SCANS_PER_MINUTE = 6
AREA = pi
OUTLET_CV = 21.24
HEAT_CAPACITY = 1000 * 4.18
COIL_UA = 72000.0
RANGEABILITY = 50.0
LOSS_UA = 500.0
FLOW_SPAN = 3000.0

# One-second substep lag coefficients.
TEMP_LAG, FLOW_LAG, LEVEL_LAG, POSITIONER_LAG = (
    1 - exp(-1 / seconds) for seconds in (120.0, 2.0, 8.0, 3.0)
)


@dataclass(frozen=True)
class ScanInput:
    feed_m3_h: float = 15.0
    feed_deg_c: float = 25.0
    ambient_deg_c: float = 20.0
    pressure_barg: float = 6.0
    temp_sp: float = 75.0
    level_sp: float = 2.0
    temp_mode: int = 1
    flow_mode: int = 2
    level_mode: int = 1
    temp_offset: float = 0.0
    flow_offset: float = 0.0
    level_offset: float = 0.0


@dataclass
class TankState:
    """Private checkpoint. Round-trip with dataclasses.asdict and TankState(**data)."""

    at_seconds: int
    temp: float
    level: float
    steam: float
    steam_valve: float
    outlet_valve: float
    temp_output: float
    flow_output: float
    level_output: float
    flow_sp: float
    temp_sensor: float
    flow_sensor: float
    level_sensor: float
    temp_error: float = 0.0
    flow_error: float = 0.0
    level_error: float = 0.0
    temp_mode: int = -1
    flow_mode: int = -1
    level_mode: int = -1


DESCRIPTOR = {
    "version": VERSION,
    "type": "equipment.tk101.v1",
    "instance_schema": {},
    "state_schema": {
        name: "integer" if kind is int else "number"
        for name, kind in TankState.__annotations__.items()
    },
    "handles": {},
    "emits": {},
    "parameters": {},
    "policies": {},
    "relations": {},
    "requires_adapters": [],
    "provides_adapters": [],
    "provenance": "/data/rca-eval/base/sim.py:plant",
}


def _steam_properties(pressure):
    saturation = 100 * (pressure + 1.013) ** 0.25
    return saturation, 2500 - 2.4 * saturation


def initial_state(inputs: ScanInput, at_seconds: int = 0) -> TankState:
    """Start at the reference steady state for the first scan's inputs."""
    temp, level = inputs.temp_sp, inputs.level_sp
    saturation, latent_heat = _steam_properties(inputs.pressure_barg)
    duty = (HEAT_CAPACITY * inputs.feed_m3_h / 3.6 * (temp - inputs.feed_deg_c)
            + LOSS_UA * (temp - inputs.ambient_deg_c))
    steam_valve = 1 + log(duty / (COIL_UA * (saturation - temp))) / log(RANGEABILITY)
    steam = duty / (latent_heat * 1000) * 3600
    outlet_valve = inputs.feed_m3_h / (OUTLET_CV * sqrt(level))
    return TankState(
        at_seconds=at_seconds, temp=temp, level=level, steam=steam,
        steam_valve=steam_valve, outlet_valve=outlet_valve,
        temp_output=steam / FLOW_SPAN * 100, flow_output=steam_valve * 100,
        level_output=outlet_valve * 100, flow_sp=steam,
        temp_sensor=temp, flow_sensor=steam, level_sensor=level,
    )


def _pi(output, error, previous_error, gain, integral_seconds):
    return min(100.0, max(0.0, output + gain * (error - previous_error + error / integral_seconds)))


def step_scan(state: TankState, inputs: ScanInput):
    """Return (next checkpoint, controller sample, private truth) at scan close.

    Process values are sampled at the same points as the reference. The
    controller PVs are the last substep's lagged, offset-adjusted readings.
    """
    s = replace(state)
    saturation, latent_heat = _steam_properties(inputs.pressure_barg)
    for _ in range(SCAN_SECONDS):
        s.temp_sensor += TEMP_LAG * (s.temp - s.temp_sensor)
        s.flow_sensor += FLOW_LAG * (s.steam - s.flow_sensor)
        s.level_sensor += LEVEL_LAG * (s.level - s.level_sensor)
        temp_pv = s.temp_sensor + inputs.temp_offset
        flow_pv = s.flow_sensor + inputs.flow_offset
        level_pv = s.level_sensor + inputs.level_offset

        if inputs.temp_mode == 1:
            error = inputs.temp_sp - temp_pv
            previous = s.temp_error if s.temp_mode == 1 else error
            s.temp_output = _pi(s.temp_output, error, previous, 4.5, 900)
            s.temp_error = error
        s.temp_mode = inputs.temp_mode

        if inputs.flow_mode == 2:
            s.flow_sp = s.temp_output / 100 * FLOW_SPAN
        if inputs.flow_mode >= 1:
            error = s.flow_sp - flow_pv
            previous = s.flow_error if s.flow_mode >= 1 else error
            s.flow_output = _pi(s.flow_output, error, previous, 0.015, 60)
            s.flow_error = error
        s.flow_mode = inputs.flow_mode

        if inputs.level_mode == 1:
            error = level_pv - inputs.level_sp
            previous = s.level_error if s.level_mode == 1 else error
            s.level_output = _pi(s.level_output, error, previous, 200.0, 600)
            s.level_error = error
        s.level_mode = inputs.level_mode

        s.steam_valve += POSITIONER_LAG * (s.flow_output / 100 - s.steam_valve)
        s.outlet_valve += POSITIONER_LAG * (s.level_output / 100 - s.outlet_valve)
        duty = COIL_UA * RANGEABILITY ** (s.steam_valve - 1) * (saturation - s.temp)
        s.steam = duty / (latent_heat * 1000) * 3600
        outflow = OUTLET_CV * s.outlet_valve * sqrt(max(s.level, 0.0))
        level_delta = (inputs.feed_m3_h - outflow) / 3600 / AREA
        temp_delta = (
            HEAT_CAPACITY * inputs.feed_m3_h / 3.6 * (inputs.feed_deg_c - s.temp)
            + duty - LOSS_UA * (s.temp - inputs.ambient_deg_c)
        ) / (HEAT_CAPACITY * 1000 * AREA * s.level)
        s.level += level_delta
        s.temp += temp_delta

    s.at_seconds += SCAN_SECONDS
    observed = {
        "at_seconds": s.at_seconds,
        "TIC-101.PV": temp_pv, "FIC-201.PV": flow_pv, "LIC-101.PV": level_pv,
        "TIC-101.OP": s.temp_output, "FIC-201.OP": s.flow_output, "LIC-101.OP": s.level_output,
        "TIC-101.SP": inputs.temp_sp, "FIC-201.SP": s.flow_sp, "LIC-101.SP": inputs.level_sp,
        "TIC-101.MODE": s.temp_mode, "FIC-201.MODE": s.flow_mode, "LIC-101.MODE": s.level_mode,
    }
    truth = {
        "at_seconds": s.at_seconds, "temp": s.temp, "level": s.level,
        "steam": s.steam, "outflow": outflow, "steam_valve": s.steam_valve,
        "outlet_valve": s.outlet_valve,
    }
    return s, observed, truth


def step_minute(state: TankState, scans):
    """Run six successive inputs for one engine minute, preserving all samples."""
    scans = tuple(scans)
    if len(scans) != SCANS_PER_MINUTE:
        raise ValueError("an engine minute requires six TK-101 scan inputs")
    observed, truth = [], []
    for inputs in scans:
        state, sample, private_sample = step_scan(state, inputs)
        observed.append(sample)
        truth.append(private_sample)
    return state, observed, truth
