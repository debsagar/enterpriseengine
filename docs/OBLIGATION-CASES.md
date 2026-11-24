# Warehouse shortage and managed services SLA

These offline examples cover a warehouse shortage and a managed services
restoration deadline under a service-level agreement (SLA). Each follows two
roles through a handoff and restarts the engine with outstanding work. A
read-only fixture supplies dated warehouse records or service desk observations.

Run from the checkout with the employee obligation API available. Use a fresh
directory for each app and the same directory for both cutoffs.

```sh
python3 -m examples.obligation_cases warehouse --run ./runs/shortage --until 19
python3 -m examples.obligation_cases warehouse --run ./runs/shortage --until 27
python3 -m examples.obligation_cases sla --run ./runs/restoration --until 19
python3 -m examples.obligation_cases sla --run ./runs/restoration --until 27
```

| Business minute | Warehouse shortage | Managed services restoration SLA |
| --- | --- | --- |
| 10 | The picker opens two follow-ups from native shortages of 12 units. Initial due minute is 20. | Triage opens two follow-ups from outage tickets with a contractual restoration deadline of 20. |
| 12 | An ETA of 24 informs the handoff to receiving, with a new follow-up due at 26. | An ETA of 24 informs the handoff to on-call. The contractual deadline stays at 20. |
| 13–19 | The new owner can see the work. The ETA leaves both cases pending. | The new owner can see the work. The ETA leaves both cases pending. |
| 20 | Receiving owns both cases, due at 26. | Both cases miss the restoration deadline. On-call receives both due events. |
| 24 | Receiving confirms eight released units for case A and three for case B. Both cases stay open. | A service probe confirms reachability and a successful transaction for case A. Case B is reachable but fails the transaction and stays open. |
| 25 | A second receipt releases four units for case A, bringing the confirmed total to 12 and closing the case. | Case B stays open. |
| 26–27 | Only case B prompts receiving and appears in the outstanding list. A redelivery of case A's final receipt records another check. | Only case B appears in the outstanding list. A redelivery of case A's probe records another check. |

The handler opens work with `run.open_obligation(key, owner=role, due=minute,
evidence=ref)` and stores the stable ID it returns. It transfers the work with
`run.handoff_obligation(obligation_id, owner=role, due=minute)`. Due values
are absolute minutes on the engine clock. Both handlers receive
`obligation_due` with payload `{"id": obligation_id, "key": key}` under the
owning role on its first available shift minute at or after the deadline.
`run.obligations()` gives the current role's outstanding work.

The handler calls `run.observe(role, as_of=run.now)` and checks the record's
source, case, kind and outcome. Warehouse follow-ups take their required unit
count, item, lot and unit of measure from the confirmed opening shortage.
Receiving credits a matching scan once per native ID, after `run.confirm`
reads back the accepted quantity and verifies the stock is available to pick.
Partial receipts accumulate across restarts. These fixtures use whole-unit counts
and distinct native IDs for separate receipts.

When the shortage is covered or the service probe verifies restoration, the
handler passes the confirmed `{"system": ..., "native_id": ...}` reference to
`run.resolve_obligation(obligation_id, evidence=ref)`. The final receipt links the
warehouse closure to the scan that covered the balance. Each delivered check
has its own event ID in history. A redelivery after closure records
`already_resolved` without another confirmation.

The examples exercise the engine's durable deadlines, role-scoped observations,
case memory, native readback and outstanding-work reminders. Acceptance tests
cover misleading evidence, partial receipts, redelivery and a crash after
native confirmation but before closure commits.

```sh
uv run python -m unittest discover -s contracts -p test_obligation_cases.py -v
```
