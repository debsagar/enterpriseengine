# Engine split map

Norvale is a simulated manufacturer. Its full company run uses a controller
that seeds the company and runs its daily work against ERPNext, Odoo, InvenTree
and QMS. Norvale also has a smaller sales consumer that runs on the generic
engine. The full controller opens a generic engine under `scheduled-additions`
for work such as timed manufacturing, quality checks and sales extensions.
The incremental CLI opens the generic engine directly for its own run.
[Company controller](../packages/environments/src/environments/norvale/native/company.py#L94) ·
[Recurring queue](../packages/environments/src/environments/norvale/native/recurring_extensions.py#L116) ·
[Incremental CLI](../packages/environments/src/environments/norvale/native/incremental.py#L90) ·
[Sales consumer](../packages/environments/src/environments/consumers/norvale.py#L24)

The component catalog describes reusable Norvale identities and factory types.
Some factory descriptors declare event names and payload schemas. The
descriptors supply no executable handlers for the full Norvale operating loop.
[Norvale declarations](../packages/components/src/components/norvale.py) ·
[Factory declarations](../packages/components/src/components/factory.py)

## What the generic engine already does

| Capability | Functions in `packages/engine/src/engine/__init__.py` |
| --- | --- |
| Clock | [`run`, line 587](../packages/engine/src/engine/__init__.py#L587) advances in integer business minutes through an inclusive cutoff and can record idle time. [`_shift_start` and `_completion`, lines 435 and 452](../packages/engine/src/engine/__init__.py#L435) account for role shifts and elapsed work. |
| Checkpoint | [`_load` and `_restore`, lines 235 and 240](../packages/engine/src/engine/__init__.py#L235) reopen saved state. [`_save`, line 381](../packages/engine/src/engine/__init__.py#L381) commits state, queue changes and history in SQLite. |
| Extend | [`extend` and `_extend`, lines 300 and 304](../packages/engine/src/engine/__init__.py#L300) append systems, roles, products and events as a validated batch with replay by extension ID. |
| Events | [`schedule`, line 521](../packages/engine/src/engine/__init__.py#L521) admits events and can book staff. [`run` and `_step`, lines 587 and 617](../packages/engine/src/engine/__init__.py#L587) order work by minute, priority and sequence, dispatch handlers and support deferred completion. |
| Native hooks | [`_transport`, line 834](../packages/engine/src/engine/__init__.py#L834) selects the host transport. [`observe`, line 884](../packages/engine/src/engine/__init__.py#L884) reads role-visible evidence as of a minute. [`write` and `confirm`, lines 1131 and 1135](../packages/engine/src/engine/__init__.py#L1131) journal native writes or confirm existing records with readback. |
| Provider hooks | [`call_provider`, line 950](../packages/engine/src/engine/__init__.py#L950) calls the injected JEV or model provider during an event and journals requests and outcomes for replay. |
| Obligations | [`open_obligation`, `handoff_obligation`, `reassign_obligation`, `resolve_obligation` and `obligations`, lines 736–825](../packages/engine/src/engine/__init__.py#L736) manage owners, deadlines, reminders, evidence and audit history. |

## What the full Norvale controller still owns

The paths in the table are relative to
[`packages/environments/src/environments/norvale/native`](../packages/environments/src/environments/norvale/native).
Where a responsibility has two proposed homes, the environment keeps company settings, permissions and integrations.

| Responsibility | Where | Generic equivalent | Proposed home | Risk |
| --- | --- | --- | --- | --- |
| Seed company, staff accounts, workflows, opening stock and orders | [`company.py:269`](../packages/environments/src/environments/norvale/native/company.py#L269) `seed`, [`:1469`](../packages/environments/src/environments/norvale/native/company.py#L1469) `recurring_setup`, [`:1537`](../packages/environments/src/environments/norvale/native/company.py#L1537) `seed_opening_orders` | `extend` can register local roles and products. Native commissioning has no equivalent. | Norvale environment | High. Repeated setup must preserve native IDs, permissions and opening balances. |
| Daily and fixture stage order, single-step and absolute day stop, scenario horizon and demand changes | [`company.py:2809`](../packages/environments/src/environments/norvale/native/company.py#L2809) `recurring_events`, [`:2836`](../packages/environments/src/environments/norvale/native/company.py#L2836) `through` / `extend`, [`:2909`](../packages/environments/src/environments/norvale/native/company.py#L2909) `fixture_events`, [`:2960`](../packages/environments/src/environments/norvale/native/company.py#L2960) `step` / `_step` | `schedule` and `run` provide ordering and cutoffs. `extend` adds events but does not change scenario demand. | Engine for dispatch and cutoffs. Norvale environment for phase order, step results and scenario changes. | High. Same-minute ordering, cutoff behavior and stage IDs must survive restart. |
| JSON checkpoint, completed stage IDs, native clock and queued additions | [`company.py:98`](../packages/environments/src/environments/norvale/native/company.py#L98) `__init__`, [`:203`](../packages/environments/src/environments/norvale/native/company.py#L203) `persist`, [`:3011`](../packages/environments/src/environments/norvale/native/company.py#L3011) `run_phase`, [`:3046`](../packages/environments/src/environments/norvale/native/company.py#L3046) `advance_clock` / `run_additions` | SQLite checkpoint and business minute. No native clock synchronization or Norvale JSON checkpoint reader. | Engine for queue and progress. Norvale environment for checkpoint compatibility, clock translation and collection hooks. | High. A committed native action can outlive a failed local checkpoint. |
| Role reads, ERP document mapping and submit/readback, mail and retries | [`company.py:184`](../packages/environments/src/environments/norvale/native/company.py#L184) `recover_company_doc`, [`:212`](../packages/environments/src/environments/norvale/native/company.py#L212) `write` / `mapped`, [`:606`](../packages/environments/src/environments/norvale/native/company.py#L606) `mail`, [`:1180`](../packages/environments/src/environments/norvale/native/company.py#L1180) `observe`, [`:3058`](../packages/environments/src/environments/norvale/native/company.py#L3058) `run_all` | `observe`, `write` and `confirm` cover transport, intent and readback. ERP draft/submit, mail reconciliation and retry rules are adapter behavior. | Component for reusable adapters. Norvale environment for role bindings and document mappings. | High. Lost replies must never cause a second submit or delivery. |
| Decision rules, JEV routing, role judgment and effective policy history | [`company.py:439`](../packages/environments/src/environments/norvale/native/company.py#L439) `decision`, [`:1223`](../packages/environments/src/environments/norvale/native/company.py#L1223) `effective_policies` / `adopt_policy`, [`:1266`](../packages/environments/src/environments/norvale/native/company.py#L1266) `choose` | `call_provider` and saved consumer decisions provide replay. No company policy or escalation rules. | Component for reusable decision behavior. Norvale environment for policies, prompts and authority. | High. Outages must leave decisions pending and replay must use the original evidence. |
| Demand, material planning, purchasing and receipts | [`company.py:1586`](../packages/environments/src/environments/norvale/native/company.py#L1586) `regime` / `demand`, [`:1828`](../packages/environments/src/environments/norvale/native/company.py#L1828) `replenishment`, [`:1986`](../packages/environments/src/environments/norvale/native/company.py#L1986) `approvals`, [`:2196`](../packages/environments/src/environments/norvale/native/company.py#L2196) `arrivals` | Seeded world events and scheduling. No purchasing or inventory policy. | Component for reusable planning and supplier behavior. Norvale environment for demand inputs and purchasing rules. | High. Duplicate orders or receipts change stock and later production. |
| Manufacturing, lot inspection, release, shipment and accounting | [`company.py:2242`](../packages/environments/src/environments/norvale/native/company.py#L2242) `inspect_release`, [`:2353`](../packages/environments/src/environments/norvale/native/company.py#L2353) `plan_production`, [`:2523`](../packages/environments/src/environments/norvale/native/company.py#L2523) `fulfillment`, [`:2619`](../packages/environments/src/environments/norvale/native/company.py#L2619) `settlements`, [`company_erp.py:45`](../packages/environments/src/environments/norvale/native/company_erp.py#L45) `CompanyERP` | No workflow equivalent. The engine supplies scheduling and confirmed writes. | Component for reusable work, quality and settlement logic. Norvale environment for revision, lot, dispatch and banking rules. | High. Preserve authority, lot quantities and links, and recovery between independent applications. |
| Maintenance, management review and lake collection | [`company.py:2654`](../packages/environments/src/environments/norvale/native/company.py#L2654) `service`, [`:2716`](../packages/environments/src/environments/norvale/native/company.py#L2716) `management`, [`:3011`](../packages/environments/src/environments/norvale/native/company.py#L3011) `run_phase` | Events, scoped observations and obligations are building blocks. No service policy, review workflow or Delta collection. | Component for reusable equipment behavior. Norvale environment for review, native tickets and collection schedules. | High. Restarts must preserve blocked equipment, evidence cutoffs and pending work. |

## Offline behavior baseline

Commands ran from the repository root on Python 3.11.16 with
`NORVALE_OFFLINE=1` and `NORVALE_RELEASE="$PWD"`.

| Command | Result |
| --- | --- |
| `uv run --no-sync --offline python -m unittest discover -s contracts` | 221 passed, 0 failed. |
| `uv run --no-sync --offline python -m unittest discover -s packages/environments/src/environments/norvale/integrations -t packages/environments/src` | 114 passed, 0 failed, 6 skipped (retained Delta archive unavailable). |
| `uv run --no-sync --offline python -m unittest discover -s packages/engine/tests` | 16 passed, 0 failed. |

## Proposed move order and proving tests

Each step is a separate PR. The named modules are the focused gates for that
step. Run all three suites after each step. For a change to the full operating
loop, R17 also calls for comparing a 20–30 simulated-day run without model calls
against a saved summary.

1. **Move one recurring phase to the existing generic queue.** Start with the day-end `native-clock` marker on the timed path. Put the marker after the day’s timed work, with the lake scan in a Norvale handler. Add a parity case that compares phase IDs, minutes, absolute day cutoffs and completion after interrupted restart. `contracts.test_norvale_recovery`, `contracts.test_recurring_extensions`, `environments.norvale.integrations.company_run.test_timing`, `environments.norvale.integrations.company_run.test_scheduled_lake`.
2. **Move recurring dispatch and progress to the same queue.** Move the remaining phases in their current order. Encode the existing pre-phase drains in queue ordering so handlers never dispatch the queue recursively. Test opening an existing checkpoint, keeping stage IDs, notebook step results and scheduled-additions history. `contracts.test_norvale_recovery`, `contracts.test_engine_storage`, `contracts.test_recurring_extensions`, `environments.norvale.integrations.company_run.test_timing`.
3. **Migrate one native write path.** Use the existing transport and journal for one ERP draft/readback path, with its native lookup key and role binding. Compare lost-reply recovery and reject changed evidence. `contracts.test_incremental_cli`, `contracts.test_engine_journal`, `contracts.test_company_erp`.
4. **Route one purchase decision through the provider hook.** Keep the decision rules in Norvale and compare observations, outputs and pending state across provider failures. `contracts.test_purchase_outage`, `contracts.test_norvale_recovery`, `contracts.test_consumer_invariants`.
5. **Extract one component workflow at a time.** Start with equipment availability for maintenance, then repeat the same process in separate PRs for lot flow and settlement. Register each behavior as Norvale starts using it. Compare the native IDs and linked records, released stock and accounting totals. `contracts.test_component_catalog`, `contracts.test_company_maintenance`, `contracts.test_company_bulk`, `contracts.test_production_profiles`, `contracts.test_company_erp`, `contracts.test_company_settlement`, `contracts.test_sales_capture`.
