# Components

A component is a data declaration, not a second simulation engine. It tells the registry what one kind of company object needs: configuration, saved state, events, relations and adapters. The engine owns time and persistence; an environment chooses which components make up a company.

The bundled catalog is loaded with:

```python
from components.catalog import manifest, registry

resolved = registry().resolve(manifest(instances))
```

`instances` is a list of versioned JSON-shaped dictionaries. Each has an ID, type, `config`, `state`, `parameters`, `policies` and `relations`. A relation names another instance with the type declared by its component. Invalid types, state, links and adapter requirements fail before a run starts.

`components.norvale` contains the basic Norvale site, role, product and customer declarations. `components.factory` contains generic workstation, warehouse, supplier, purchase-order, work-order and quality-check declarations. They are declarations only: attach behavior in the environment or consumer that uses them.
