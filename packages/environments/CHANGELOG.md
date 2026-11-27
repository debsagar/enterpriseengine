# Changelog

## [Unreleased]

### Added

- Role contracts (`native/behaviour_roles.py`, `docs/BEHAVIOUR.md`): one registry and one daily `role_cases` phase, enabled by `"behaviour_roles": true` in an operating run's config (set at provision with `NORVALE_BEHAVIOUR_ROLES=1`). QA manager (close, keep open or escalate a non-conformance on a rejected lot), finance (approve, hold or request information on a supplier invoice; settlement pays only approved ones), planner (wait, reschedule or escalate a shortage, replacing the code-written task) and process engineer (raise or decline a design change after repeated non-conformances) each open one case per record, decide it (offline rule when `NORVALE_OFFLINE=1`), execute and read back the write, and escalate past-due cases.
- With role contracts on, the daily chat has one message per decided case, naming the record, instead of the fixed status post; `behaviour.json` gains `run_targets` with the BEHAVIOUR.md run-level targets as pass/fail.
- `norvale-simulate` runs use a fixed layout: `run.json`, `verification.json`, `observable/lake`, `observable/days/<date>/manifest.json` and `private/{trace,reports,logs}`. It exits non-zero unless every requested day has a manifest and a clean end-of-day scan, no source failed, no lake row lacks a business date and every business-chain entity has records.
- Operating runs post a code-written daily operations status to a Matrix room and put the next day's review in the planner's calendar. The IT administrator can read all employee records.
- `norvale-simulate --offline` runs the native services with deterministic role decisions and no provider credentials.
- Newly commissioned operating runs also archive ERP items, customers, suppliers, employees, batches, warehouses, supplier quotations, journal entries and tasks, MES manufacturing orders in every state and MES BOMs, and maintenance tickets with comments. `integrations/lake/flows.py` lists the entities of each business chain.
- Lake snapshots appended by the company collector carry a `business_date`: the record's own simulated-clock stamp when it advances, otherwise the day the collector observed it (QMS, MES and PLM stamp wall-clock time). Daily manifests select by that date and list each record's `content_hash`, so a day resolves to exact versions.
- Decision questions name only the graded actions; a reply of `defer` or `investigate` is read as `request_info` instead of stopping the run.
- Operating runs report daily behaviour numbers in `<run>/behaviour.json` and the heartbeat: approval rate by case type, standing policies proposed and rejected, approvals held by code and approvals that overrode a contradiction flag.
- Set `NORVALE_COMMS_MODEL` to have a small model word the operating run's employee emails (unset keeps the templates). The decision headline stays code-written; a draft that drops or invents a record name, item code, date or number is discarded for the template.
- A 40-case labelled decision set (`native/decision_cases.json`) and `python -m environments.norvale.native.score_decisions --model NAME --out FILE`, which scores one model on wrong approvals, wrong refusals and usage per call.
- Role decisions are graded (`approve`, `approve_with_condition`, `request_info`, `reject`), cite evidence paths and receive a role card with duty, authority limits and escalation. Each outcome writes ERP records and mails the requester; conditions become tasks, rejected reorder requests are cancelled. Approvals without a resolvable citation, without a stated condition, or above the role limit are held as `request_info`.
- Role decision prompts carry code-checked facts (days of cover, stock against reorder target, PO-minus-quote deltas, approval-limit headroom), explicit contradiction flags, effective policies and the role's written limits. Routing sees the same flags.
- Long-run safety for operating runs: `<run>/heartbeat.json` after every stage and on a stop; a stall guard (`stall_minutes`, default 30); a spend total in the checkpoint with an owner ceiling (`NORVALE_SPEND_CEILING_USD`, JEV priced by `NORVALE_JEV_USD_PER_MTOK`); and a preflight (an OpenRouter endpoint, both limits, 20 GB free, a creatable Docker network) before a live operating run starts. An unpriced call blocks further paid calls under a ceiling until `NORVALE_UNPRICED_CALLS_RECONCILED` covers it.
- Operating runs label every `company_erp_pending` error `expected` or `bug` and stop at the end of a business day with more bug-class errors than `daily_bug_limit` (config, default 5).
- Add point-in-time lake snapshot manifests and readers for mail, calendar, Matrix messages, and master directory records.
- The operating company's scheduled lake collector now reads mail, calendars, Matrix rooms and the staff directory, and saves a manifest per business date under `lake/manifests/`, naming any source that failed that pass. Newly commissioned runs only.
- Run the TK-101 `process.tank` plant on the Norvale engine clock (`environments.consumers.tank`): its `PlantState` is registered with the engine so `Engine.fork()` copies it, and a low alarm becomes a `tank_boundary` event. Enable with a `tank` section in the run config.
- Run TK-101 inside a Norvale company loop, offline: the company's setup registers `process.tank`; the `tank_run`, `tank_boundary`, `tank_operator` and `tank_lot` events go through the company's handler (`norvale.native.company_tank`). A configured role (`tank.role`) applies a named policy (`tank.policies`) to decide pause or continue and the decision keeps its inputs. Each inspected lot writes a QMS inspection with its AI-101 reading, an ERP batch release or hold, and the sales-order shortfall computed by `CompanyERP`, plus one shift message, into offline QMS, ERP and mail doubles; every record carries the causing `engine` event ID.
- Add `Engine.fork_run` to the Norvale company, which forks the engine with a `snapshot_external` hook that copies the offline doubles and refuses when live application stacks are attached. A forked run keeps its source's namespace (`origin_run` in `config.json`).
- Bind saved Norvale runs and incremental manifests to package versions.

### Changed

- A purchase inside a standing policy's scope is no longer always approved on the routine path: it is rejected when it differs from its supplier quote, and deferred when that quote has expired.
- Run Norvale's daily phases as ordered events on the engine's queue; the controller's state now lives in the engine checkpoint, and `state.json` is read once to seed a pre-engine run and never written.
- Correct source paths and commands in the Norvale integration guides.

### Fixed
- Matrix lake ingestion follows advancing cursors across empty pages so later room messages reach the archive.

- A rejected reorder-level request no longer blocks its item for the rest of a run: after 5 days a continuing shortage raises a new request.
- Seed opening stock directly into Raw Materials and make it available before day-0 production. It is not lab-sampled; any QMS inspection record or quarantined quantity withdraws the exemption, and MES staging accepts it without a release.
- Work using inspected opening stock can resume after ERP sync confirms a QMS release covering the reserved inputs.
- A purchase or reorder-level case with no decision after 3 reviews is deferred, escalated to the CFO as a task and not reviewed again; a deferred draft order no longer counts as inbound supply.
- The role model's API key, endpoint and model now come from one provider; with both keys set, the OpenAI key was sent to OpenRouter. OpenRouter now wins.
- TK-101 no longer rebuilds its full-horizon scan input lists on every plant advance, so the time per simulated day no longer grows with the configured horizon.
- Restaging a fork snapshot replaces its previous lake records and manifest, keeping later-day data out of earlier snapshots.
- Register a PLM product family for every seeded SKU during Norvale operating setup, so day-0 inspections no longer fail with "No registered PLM family".
- Avoid unsupported tuple unpacking in the Frappe QMS lease-dispatch script.

## [0.1.0] - 2026-11-24

### Added

- Initial independently versioned environments release candidate.
