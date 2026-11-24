# Inputs for a new company

## Python objects

| Input to Engine | Meaning |
|---|---|
| `directory` | A private local checkpoint directory. Reuse it to resume, or choose an empty directory for another scenario. |
| `consumer` | Your Python object with a JSON-compatible `config` dictionary and `handle(run, kind, payload)`. It supplies the meaning of events and business rules. Keep the class import path, seed and config stable on resume. |
| `seed` | Seed for modeled random processes. External services and provider answers have their own state. |
| `native` | Default application transport. Supply `None` for a state-only simulation; a live transport implements send, find, read and records. |
| `providers` | Optional host object exposing `call(tier, prompt, tools)` for JEV and model calls. Use `None` for deterministic work. |
| `monotonic` | Wall-time measurement callable in seconds, usually `time.monotonic`. Business time is an integer count of minutes. |
| `adapters` | Optional named adapter factories for connected systems. The host controls connection targets and credentials. |

## Event and catalog JSON

`run.extend(batch_id, **sections)` accepts the following append-only sections. A JSON command uses `id` for the first argument. Omit unused sections. Publishing a batch and admitting its events is one transaction.

| Section | Shape and effect |
|---|---|
| `roles` | Object keyed by role name. Each entry requires daily `shifts` of `[start, end)` minutes and can set a positive `staff` count and `permissions` such as `write:desk:order`. |
| `systems` | Object keyed by instance name. A generic entry names a registered `adapter` and its settings. An application-owned host may accept named connections and validate them before admission. |
| `products` | Object keyed by product name. Each entry names a `system` and its native `item` ID. Native commissioning remains the operator's responsibility. |
| `events` | Array of objects. Required: unique `id`, handler `kind`, integer `at`, object `payload`. Optional: `role`, `priority`, `duration`, `elapsed`. Lower priorities run first at a tie. A duration of zero completes at its admitted start. |

Use the [opening JSON](examples/first-run/opening.json) as the smallest example and the [sales desk extension](examples/norvale-sales/extension.json) for a complete site, employee role, product and action. Extensions admit new work at or after the current business minute. Existing names cannot be replaced. Norvale's [scenario JSON](../packages/environments/src/environments/norvale/scenarios/norvale-q1.json) belongs to its native launcher and describes a company's opening orders.
