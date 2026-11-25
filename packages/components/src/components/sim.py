"""Steppable heated tank, ported from HistorianRCA's TK-101 simulator."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, replace
from typing import NamedTuple

from .registry import VERSION


@dataclass(frozen=True)
class PlantParameters:
    """Physical and controller parameters in the reference plant's units."""

    A: float = math.pi
    Cv: float = 21.24
    rcp: float = 1000 * 4.18
    UAc: float = 72000.0
    R: float = 50.0
    UAl: float = 500.0
    KcT: float = 4.5
    TiT: float = 900.0
    KcF: float = 0.015
    TiF: float = 60.0
    KcL: float = 200.0
    TiL: float = 600.0
    Fspan: float = 3000.0
    ptau: float = 3.0
    lagT: float = 120.0
    lagF: float = 2.0
    lagL: float = 8.0
    dt_scan: float = 10.0


@dataclass(frozen=True)
class PlantInputs:
    """Scan-indexed feed, utilities, setpoints, modes and transmitter offsets."""

    Ff: Sequence[float]
    Tf: Sequence[float]
    Ta: Sequence[float]
    Ph: Sequence[float]
    spT: Sequence[float]
    spL: Sequence[float]
    mT: Sequence[int]
    mF: Sequence[int]
    mL: Sequence[int]
    nT: Sequence[float]
    nF: Sequence[float]
    nL: Sequence[float]
    parameters: PlantParameters = PlantParameters()
    nA: Sequence[float] | None = None  # Analyzer noise plus calibration offset, in percentage points.


@dataclass
class PlantState:
    """Checkpoint as JSON values via dataclasses.asdict and PlantState(**data)."""

    h: float
    T: float
    sT: float
    sF: float
    sL: float
    opT: float
    opF: float
    opL: float
    spF: float
    w: float
    xTV: float
    xLV: float
    purity_delay: list[float]
    purity_lag: float = 0.0
    analyzer: float = 0.0
    eT0: float = 0.0
    eF0: float = 0.0
    eL0: float = 0.0
    pmT: int = -1
    pmF: int = -1
    pmL: int = -1
    k: int = 0  # Next unprocessed scan.


class HistorianSample(NamedTuple):
    """Reference plant output order, followed by the analyzer reading."""

    T: float
    h: float
    w: float
    Fout: float
    xTV: float
    xLV: float
    opT: float
    opF: float
    opL: float
    spF: float
    pvT: float
    pvF: float
    pvL: float
    analyzer: float


DESCRIPTOR = {
    "version": VERSION,
    "type": "process.tank",
    "instance_schema": {},
    "state_schema": {
        field.name: {int: "integer", float: "number", list[float]: "array"}[field.type] for field in fields(PlantState)
    },
    "handles": {},
    "emits": {"process.tank.boundary": {"kind": "string", "k": "integer", "alarms": "object", "changes": "object"}},
    "parameters": dict.fromkeys((field.name for field in fields(PlantParameters)), "number"),
    "policies": {},
    "relations": {},
    "requires_adapters": [],
    "provides_adapters": [],
    "provenance": "HistorianRCA process description and tag definitions, via rca-eval/base/sim.py",
}

_SCAN_INPUTS = ("Ff", "Tf", "Ta", "Ph", "spT", "spL", "mT", "mF", "mL", "nT", "nF", "nL")
_CONTROLS = ("spT", "spL", "mT", "mF", "mL")
_CHANNELS = {name: index for index, name in enumerate(HistorianSample._fields)}
_CHANNELS["AI-101"] = _CHANNELS["analyzer"]


def initial_state(inputs: PlantInputs) -> PlantState:
    """Initialize controllers and valve positions at the first scan's steady state."""
    p = inputs.parameters
    h, T = float(inputs.spL[0]), float(inputs.spT[0])
    Ff, Tf, Ta, Ph = (float(getattr(inputs, name)[0]) for name in ("Ff", "Tf", "Ta", "Ph"))
    Tsat = 100 * (Ph + 1.013) ** 0.25
    hfg = 2500 - 2.4 * Tsat
    Q = p.rcp * Ff / 3.6 * (T - Tf) + p.UAl * (T - Ta)
    xTV = 1 + math.log(Q / (p.UAc * (Tsat - T))) / math.log(p.R)
    w = Q / (hfg * 1000) * 3600
    xLV = Ff / (p.Cv * math.sqrt(h))
    return PlantState(
        h=h,
        T=T,
        sT=T,
        sF=w,
        sL=h,
        opT=w / p.Fspan * 100,
        opF=xTV * 100,
        opL=xLV * 100,
        spF=w,
        w=w,
        xTV=xTV,
        xLV=xLV,
        purity_delay=[],
    )


def step_until_event(
    state: PlantState, inputs: PlantInputs, max_steps: int, thresholds: Mapping[str, float]
) -> tuple[PlantState, list[HistorianSample], dict]:
    """Advance towards the exclusive scan index max_steps with strict low alarms.

    A change in a scheduled mode or setpoint is applied on its scan before
    returning. The triggering scan is included in the slice. The event keeps
    both alarms and controller changes if they coincide. Resuming uses the
    same input timeline and starts with the first unprocessed scan.
    """
    start = state.k
    if not 0 <= start <= max_steps:
        raise ValueError("expected 0 <= state.k <= max_steps")
    for name in _SCAN_INPUTS:
        if len(getattr(inputs, name)) < max_steps:
            raise ValueError(f"{name} does not cover max_steps")
    if inputs.nA is not None and len(inputs.nA) < max_steps:
        raise ValueError("nA does not cover max_steps")
    alarm_limits = [(name, _CHANNELS[name], limit) for name, limit in thresholds.items()]
    state = replace(state, purity_delay=state.purity_delay.copy())
    historian = []
    event = {"kind": "max_steps", "k": max_steps, "alarms": {}, "changes": {}}

    # A literal 10 s PI step goes unstable at high steam load. Use the
    # reference's ten internal substeps and record only completed scans.
    p = inputs.parameters
    dt = p.dt_scan / 10
    aT = 1 - math.exp(-dt / p.lagT)
    aF = 1 - math.exp(-dt / p.lagF)
    aL = 1 - math.exp(-dt / p.lagL)
    aP = 1 - math.exp(-dt / p.ptau)
    analyzer_lag = 1 - math.exp(-p.dt_scan / 90)
    analyzer_delay = round(480 / p.dt_scan)
    analyzer_period = max(1, int(300 // p.dt_scan))
    analyzer_resolution = 0.195 * 10 / 100
    controls = [(name, getattr(inputs, name)) for name in _CONTROLS]
    for k in range(start, max_steps):
        sample = _scan(state, inputs, k, dt, aT, aF, aL, aP)
        # Transport delay, sensor lag, noise/offset, quantization and sample hold.
        purity = 99.4 - 0.02 * (sample[0] - 78) ** 2
        delayed = purity
        if analyzer_delay:
            if k == 0:
                state.purity_delay.extend([purity] * analyzer_delay)
            slot = k % analyzer_delay
            delayed = state.purity_delay[slot]
            state.purity_delay[slot] = purity
        if k == 0:
            state.purity_lag = delayed
        else:
            state.purity_lag += analyzer_lag * (delayed - state.purity_lag)
        if k % analyzer_period == 0:
            noise = 0.0 if inputs.nA is None else float(inputs.nA[k])
            measured = round((state.purity_lag + noise) / analyzer_resolution) * analyzer_resolution
            state.analyzer = min(100.0, max(90.0, measured))
        row = HistorianSample(*sample, state.analyzer)
        historian.append(row)
        state.k = k + 1

        alarms = {name: row[index] for name, index, limit in alarm_limits if row[index] < limit}
        changes = {}
        if k:
            changes = {
                name: (int if name.startswith("m") else float)(series[k])
                for name, series in controls
                if series[k] != series[k - 1]
            }
        if alarms or changes:
            event = {"kind": "alarm" if alarms else "controller_change", "k": k, "alarms": alarms, "changes": changes}
            break
    return state, historian, event


def _scan(state, inputs, k, dt, aT, aF, aL, aP):
    p = inputs.parameters
    Ff, Tf, Ta, Ph, spT, spL = (
        float(series[k]) for series in (inputs.Ff, inputs.Tf, inputs.Ta, inputs.Ph, inputs.spT, inputs.spL)
    )
    mT, mF, mL = int(inputs.mT[k]), int(inputs.mF[k]), int(inputs.mL[k])
    nT, nF, nL = float(inputs.nT[k]), float(inputs.nF[k]), float(inputs.nL[k])
    h, T, w = state.h, state.T, state.w
    sT, sF, sL = state.sT, state.sF, state.sL
    opT, opF, opL, spF = state.opT, state.opF, state.opL, state.spF
    eT0, eF0, eL0 = state.eT0, state.eF0, state.eL0
    pmT, pmF, pmL = state.pmT, state.pmF, state.pmL
    xTV, xLV = state.xTV, state.xLV
    Tsat = 100 * (Ph + 1.013) ** 0.25
    hfg = 2500 - 2.4 * Tsat
    for _ in range(10):
        sT += aT * (T - sT)
        sF += aF * (w - sF)
        sL += aL * (h - sL)
        pvT, pvF, pvL = sT + nT, sF + nF, sL + nL
        if mT == 1:  # TIC, reverse acting
            e = spT - pvT
            if pmT != 1:
                eT0 = e
            opT = min(100.0, max(0.0, opT + p.KcT * (e - eT0 + e * dt / p.TiT)))
            eT0 = e
        pmT = mT
        if mF == 2:  # FIC in cascade follows the TIC output
            spF = opT / 100 * p.Fspan
        if mF >= 1:
            e = spF - pvF
            if pmF < 1:
                eF0 = e
            opF = min(100.0, max(0.0, opF + p.KcF * (e - eF0 + e * dt / p.TiF)))
            eF0 = e
        pmF = mF
        if mL == 1:  # LIC, direct acting
            e = pvL - spL
            if pmL != 1:
                eL0 = e
            opL = min(100.0, max(0.0, opL + p.KcL * (e - eL0 + e * dt / p.TiL)))
            eL0 = e
        pmL = mL
        xTV += aP * (opF / 100 - xTV)
        xLV += aP * (opL / 100 - xLV)
        Q = p.UAc * p.R ** (xTV - 1) * (Tsat - T)
        w = Q / (hfg * 1000) * 3600
        Fout = p.Cv * xLV * math.sqrt(max(h, 0.0))
        dh = (Ff - Fout) / 3600 / p.A
        dT = (p.rcp * Ff / 3.6 * (Tf - T) + Q - p.UAl * (T - Ta)) / (p.rcp * 1000 * p.A * h)
        h += dh * dt
        T += dT * dt

    state.h, state.T, state.w = h, T, w
    state.sT, state.sF, state.sL = sT, sF, sL
    state.opT, state.opF, state.opL, state.spF = opT, opF, opL, spF
    state.eT0, state.eF0, state.eL0 = eT0, eF0, eL0
    state.pmT, state.pmF, state.pmL = pmT, pmF, pmL
    state.xTV, state.xLV = xTV, xLV
    return T, h, w, Fout, xTV, xLV, opT, opF, opL, spF, pvT, pvF, pvL
