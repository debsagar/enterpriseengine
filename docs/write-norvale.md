# Write Norvale using EnterpriseEngine

For employee identities, API credentials, native permissions and approval handoffs, start with [roles and software](roles-and-software.md).

## Define the opening

The published starter opens with four customer orders totaling 720 units. This example uses the native Norvale company implementation. Change quantities and due days to change the pressure on its planner. A due day is an offset from the start of the scenario.

```python
from docs.examples.norvale import norvale_quarter
import json
from pathlib import Path

scenario = norvale_quarter(seed=42, days=90)
scenario["opening_orders"][0]["quantity"] = 360
Path("my-norvale.json").write_text(json.dumps(scenario))
```

From the checkout, inspect the customized scenario before provisioning:

```sh
uv run norvale-simulate --scenario my-norvale.json --run /data/enterprise/runs/my-norvale --plan
```

For the full company, complete the [native prerequisites](GENERATING.md) and use the same command without `--plan`. That provisions a private deployment and can incur live provider costs. A successfully commissioned run resumes from its original directory. Pick a fresh directory for a different opening.

## Where the company rules live

The reusable engine owns event time, scheduling, checkpointing and verified writes. In the [Norvale consumer](../packages/environments/src/environments/consumers/norvale.py), `handle(engine, kind, payload)` interprets company events. For an order, the consumer validates quantity and rate, resolves the native product and role, writes through the selected adapter and uses the returned record ID for follow-on work.

This is how a host wires the existing Norvale consumer to the engine for a sales-order event. The host supplies a commissioned native transport and a fresh checkpoint directory. The [incremental host](../packages/environments/src/environments/norvale/native/incremental.py) handles that setup and persists the opening plan for replay.

```python
from time import monotonic
from environments.consumers.norvale import Consumer
from engine import Engine

def open_sales_day(directory, native):
    company = Engine(directory, Consumer({}), seed=42, native=native,
                     providers=None, monotonic=monotonic)
    company.schedule("order-01", "order", at=540, role="sales",
                     payload={"case": "opening-01", "quantity": 120, "rate": 4.5})
    company.run(until=540)
    return company
```

This small API example covers one draft sales order. For the complete native manufacturing company, the [scenario launcher](../packages/environments/src/environments/norvale/simulate.py) invokes the [recurring operating loop](../packages/environments/src/environments/norvale/native/company.py). Its [ERP controller](../packages/environments/src/environments/norvale/native/company_erp.py) and [cross-application adapters](../packages/environments/src/environments/norvale/integrations/company_run) coordinate product revisions, factory execution, quality decisions, shipment and lake ingestion. Following one order reads links produced by that loop. Other orders remain in competition for stock and staff.

## Changing a running company

A supported software connection, new role, product and scheduled action can be appended together through the owning generator’s [extension interface](GENERATING.md). Existing event IDs and history stay with the run. Staff counts model parallel capacity. Individual employees require identities, permissions and behavior. Arbitrary removal, live resizing of an existing role, and a 2,000-person native deployment are outside the demonstrated contract.
