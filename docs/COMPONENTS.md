# Components

A component is a data declaration, not a second simulation engine. It tells the registry what one kind of company object needs: configuration, saved state, events, relations and adapters. The engine owns time and persistence; an environment chooses which components make up a company.

The bundled catalog is loaded with:

```python
from components.catalog import manifest, registry

resolved = registry().resolve(manifest(instances))
```

`instances` is a list of versioned JSON-shaped dictionaries. Each has an ID, type, `config`, `state`, `parameters`, `policies` and `relations`. A relation names another instance with the type declared by its component. Invalid types, state, links and adapter requirements fail before a run starts.

`components.norvale` contains the basic Norvale site, role, product and customer declarations. `components.factory` contains generic workstation, warehouse, supplier, purchase-order, work-order and quality-check declarations. They are declarations only: attach behavior in the environment or consumer that uses them.

## Heated process tank

`process.tank` models a steam-heated tank with temperature, steam-flow and level
controllers. Create the scan-indexed `PlantInputs` from feed and utility
conditions, controller modes and setpoints, and transmitter offsets. Call
`initial_state(inputs)` and then
`step_until_event(state, inputs, max_steps, thresholds)` from `components.sim`.
The reference parameters use 10-second scans.

The returned checkpoint holds the next scan index, `k`. Store it with
`dataclasses.asdict(state)` and restore it with `PlantState(**saved)`. Resume
against the same input timeline. `max_steps` is the exclusive scan index, so
advance it to extend a run. The historian slice includes any scan that
triggered an event. A mode or setpoint change is applied on its scheduled
scan. A boundary event reports every triggered alarm and scheduled change on
that scan.

Pass low alarm limits by historian channel, such as
`{"pvT": 65.0, "AI-101": 96.0}`. Each scan below an enabled limit returns an
alarm that includes the measured value. `AI-101` is the sampled purity
analyzer reading after transport delay, sensor lag and quantization. Supply
its optional noise and calibration offsets in `PlantInputs.nA`.
