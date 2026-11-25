# EnterpriseEngine

EnterpriseEngine contains a generic Python engine for scheduling work and saving simulation runs, a library of reusable component declarations, and the Norvale manufacturing environment. Norvale holds the company logic and connects to ERPNext, Odoo, InvenTree, QMS and model providers. Its full operating run still uses a separate controller. It uses the generic engine for scheduled additions, and the component declarations are available through a registry. [Engine split map](docs/ENGINE-SPLIT-MAP.md)

The small Python and notebook examples run offline.

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

See the [component guide](docs/COMPONENTS.md).

## Run the notebook

After installing the workspace, set up the notebook kernel.

```sh
uv pip install --python .venv/bin/python ipykernel
.venv/bin/python -m ipykernel install --user --name enterpriseengine
```

Open the notebook and select the `enterpriseengine` kernel. This toy demo does not run the Norvale company. [Engine tour notebook](notebooks/engine_tour.ipynb)

## Repository map

- `packages/engine`: time, scheduling, save/resume and confirmed record export.
- `packages/components`: reusable component declarations and registry.
- `packages/environments` holds company implementations, including [Norvale](packages/environments/src/environments/norvale).
- `contracts`: offline behavior and compatibility tests.
- `docs`: guides and longer architecture notes.

For the full Norvale operating loop and its limits, read [docs/NORVALE.md](docs/NORVALE.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
