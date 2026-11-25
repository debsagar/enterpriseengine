# EnterpriseEngine

A **causal digital-twin and counterfactual simulation engine** for companies.

It lets you run a company forward in time across real software (ERPNext, Gitea, QMS, mail) and physical equipment, then **fork** the timeline at any event to test *"what-if"* decisions—and see the exact downstream impact on quality, inventory, orders, and communications.

---

## Architecture: Three Packages

```
┌─────────────────────────┐     ┌──────────────────────────┐     ┌───────────────────────────┐
│     packages/engine     │     │   packages/components    │     │   packages/environments   │
│    (v0.3.0, generic)    │     │  (v0.1.0, physics/math)  │     │    (v0.1.0, companies)    │
│                         │     │                          │     │                           │
│ • Simulation clock      │◄───►│ • process.tank (TK-101)  │◄───►│ • Norvale company loop    │
│ • Priority event queue  │     │ • Continuous ODE / scans │     │ • ERP, QMS, Gitea, mail   │
│ • SQLite checkpointing  │     │ • Alarm thresholds       │     │ • Role policies & routing │
│ • Engine.fork()         │     │ • Zero company knowledge │     │ • Causal record exports   │
└─────────────────────────┘     └──────────────────────────┘     └───────────────────────────┘
```

1. **`packages/engine` (`0.3.0`)** — Domain-agnostic discrete-event engine. Manages the shared simulation clock, event queue, SQLite checkpoints, and `Engine.fork()` (deep-copying engine + registered component state into parallel branches). Knows nothing about tanks or companies.
2. **`packages/components` (`0.1.0`)** — Reusable, executable physical/process models (e.g. `process.tank` for heated tank `TK-101`). Advances in 10-second scans, tracks temperature/level/purity (`AI-101`), and emits boundary events when thresholds trip.
3. **`packages/environments` (`0.1.0`)** — Company assemblies (e.g. **Norvale**). Wires components onto the `Engine` clock, routes events to company roles, executes actions in ERP/QMS/Gitea/mail, and exports causal records.

---

## How a Counterfactual Fork Works (TK-101 Example)

```
Min 0   Normal run: steam = 6 bar, tank = 78°C, Lot L1 purity = 99.39%
  │
Min 60  Disturbance: steam pressure drops → tank begins cooling
  │
Min 75  Alarm (< 65°C): tank stops, emits `tank_boundary` onto Engine queue
  │
  ├──► Engine.fork() ──► Branch A (Policy: pause / slow feed × 0.3)
  │                        └─► L2 purity 97.46% → QMS Release → Stock 100 → 0 shortfall
  │
  └──► Engine.fork() ──► Branch B (Policy: continue / do nothing)
                           └─► L2 purity 94.48% → QMS Hold    → Stock  40 → 60 shortfall
```

Both branches share identical history and records up to the fork event, then diverge solely due to the injected role policy.

---

## Setup & Running Tests

Requires Python 3.10+ and [`uv`](https://docs.astral.sh/uv/).

```bash
# Install workspace packages in editable mode
uv sync --all-packages

# Run offline contract & engine tests (no Docker, no paid API calls)
NORVALE_OFFLINE=1 uv run pytest contracts/ packages/engine/tests/

# Lint & version checks
uv run ruff check .
uv run python scripts/check_versions.py
```

- **Offline mode (`NORVALE_OFFLINE=1`):** Disables paid LLM API calls and routes role decisions through deterministic rules (`operating.offline_decision`).
- **Live mode:** Connects to the Docker stack (ERPNext, Gitea, QMS, mail) and live model providers.

---

## Using with AI

- **JEV + LLM routing:** A lightweight router (**JEV**) checks whether an incoming situation matches an existing policy. Routine cases run deterministically; only unfamiliar or judgment-heavy cases wake the role LLM.
- **Decision model + small LLM for comms:** For high-throughput runs (e.g. 180-day datasets), use a fast decision model/policy to pick the action, and a small/fast LLM only to draft the resulting emails, DMs, and issue comments—reserving full LLMs for rare, ambiguous cases.
