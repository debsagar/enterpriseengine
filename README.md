# EnterpriseEngine

EnterpriseEngine is a Python simulation engine for making a small company run in business time. The engine schedules work and saves checkpoints. Components describe the company: its people, products, equipment, stock and work. Environments assemble those components into a company such as Norvale.

It runs offline by default. The included examples do not start Docker, call an ERP, or use a model.

## Install

```sh
git clone --branch dev https://github.com/debsagar/enterpriseengine.git
cd enterpriseengine
uv sync --locked --all-packages
```

## Install and use from Python

Save this as `my_company.py` and run `python my_company.py`. It needs no services or credentials.

```python
from time import monotonic

from enterpriseengine import Engine


class QualityDesk:
    config = {}

    def handle(self, run, kind, payload):
        if kind != "inspect":
            raise ValueError(kind)
        result = "in range" if 5.0 <= payload["ph"] <= 6.0 else "out of range"
        run.state.setdefault("inspections", {})[payload["lot"]] = [run.now, result]


company = Engine("quality-run", QualityDesk(), seed=42, native=None, providers=None, monotonic=monotonic)
company.extend(
    "opening-v1",
    roles={"analyst": {"shifts": [[540, 1020]], "staff": 1}},
    events=[
        {"id": "sample-A", "kind": "inspect", "at": 525, "duration": 10, "role": "analyst", "payload": {"lot": "A", "ph": 7.2}},
        {"id": "sample-B", "kind": "inspect", "at": 525, "duration": 10, "role": "analyst", "payload": {"lot": "B", "ph": 5.5}},
    ],
)
company.run(until=560)
print(company.state["inspections"])
```

## Run a small simulation

These are the offline commands used for this repository. They create a fresh temporary run, complete one queued review, then resume it to complete the second.

```sh
RUN_DIR=$(mktemp -d)
uv run --no-sync --offline python -m docs.examples.first_run --run "$RUN_DIR" --until 550
uv run --no-sync --offline python -m docs.examples.first_run --run "$RUN_DIR" --until 560
```

The second command prints two review records with their business start and finish minutes.

## Components

Each component is a small declaration: its type, JSON configuration and state, events, relations and any required adapter. `components.catalog.registry()` loads the bundled declarations and `Registry.resolve()` checks an environment manifest before a simulation begins. The built-in catalog includes Norvale site, role, product and customer declarations plus generic factory workstation, warehouse, supplier, purchase order, work order and quality-check declarations.

See [the component guide](docs/COMPONENTS.md) and the executed [engine tour notebook](notebooks/engine_tour.ipynb).

## Repository map

- `packages/engine`: time, scheduling, save/resume and confirmed record export.
- `packages/components`: reusable component declarations and registry.
- `packages/environments`: assembled companies; Norvale lives under `environments/norvale`.
- `contracts`: offline behavior and compatibility tests.
- `docs`: guides and longer architecture notes.

For the full Norvale operating loop and its limits, read [docs/NORVALE.md](docs/NORVALE.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
