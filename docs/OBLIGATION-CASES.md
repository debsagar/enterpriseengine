# Warehouse shortage and managed services SLA

These offline examples follow two roles through an employee obligation and
restart the engine with outstanding work. Each uses a read-only fixture as
the native application. The fixture supplies dated ERP-style warehouse records
or service desk observations. No accounts or services are required.

Run from the checkout with the employee obligation API available. Use a fresh
directory for each case and the same directory for both cutoffs.

```sh
python3 -m examples.obligation_cases warehouse --run ./runs/shortage --until 19
python3 -m examples.obligation_cases warehouse --run ./runs/shortage --until 27
python3 -m examples.obligation_cases sla --run ./runs/restoration --until 19
python3 -m examples.obligation_cases sla --run ./runs/restoration --until 27
```

| Business minute | Warehouse shortage | Managed services restoration SLA |
| --- | --- | --- |
| 10 | The picker opens two follow-ups from native shortages of eight units. Initial due minute is 20. | Triage opens two follow-ups from outage tickets with a contractual restoration deadline of 20. |
| 12 | An ETA of 24 informs the handoff to receiving, with a new follow-up due at 26. | An ETA of 24 informs the handoff to on-call. The contractual deadline stays at 20. |
| 13–19 | The new owner can see the work. The ETA leaves both cases pending. | The new owner can see the work. The ETA leaves both cases pending. |
| 20 | Receiving owns both cases, due at 26. | Both cases miss the restoration deadline. On-call receives both due events. |
| 24 | A receiving scan confirms all eight missing units are available for case A. Case B received three units and stays open. | A service probe confirms reachability and a successful transaction for case A. Case B is reachable but fails the transaction and stays open. |
| 26–27 | Only case B prompts receiving and appears in the outstanding list. | Only case B appears in the outstanding list. |

The handler opens work with `run.open_obligation(key, owner=role, due=minute,
evidence=ref)` and transfers it with `run.handoff_obligation(key, owner=role,
due=minute)`. Due values are absolute minutes on the engine clock. Both
handlers receive `obligation_due` with payload `{"key": key}` under the owning
role on its first available shift minute at or after the deadline.
`run.obligations()` gives the current role's outstanding work.

The handler calls `run.observe(role, as_of=run.now)` and checks the record's
system, case, kind and outcome. It calls `run.confirm` to read the native
record back by ID and then passes the confirmed `{"system": ..., "native_id": ...}`
reference to `run.resolve_obligation(key, evidence=ref)`. The ETA informs scheduling.
Resolution depends on the observed receipt or probe. The acceptance tests
also check that a future record is invisible and interrupt a handler after
native confirmation to exercise recovery before the resolution commits.

**Operating loop.** Blocks B and E cover durable deadlines and case memory.
Blocks D and I cover scoped observations and the owning role. Blocks J and K
cover native readback and references. Block L covers reminders and open cases.

```sh
python3 -m unittest discover -s tests -p test_obligation_cases.py -v
```
