# Changelog

## [Unreleased]

### Added

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

- The role model's API key, endpoint and model now come from one provider; with both keys set, the OpenAI key was sent to OpenRouter. OpenRouter now wins.
- TK-101 no longer rebuilds its full-horizon scan input lists on every plant advance, so the time per simulated day no longer grows with the configured horizon.
- Register a PLM product family for every seeded SKU during Norvale operating setup, so day-0 inspections no longer fail with "No registered PLM family".
- Avoid unsupported tuple unpacking in the Frappe QMS lease-dispatch script.

## [0.1.0] - 2026-11-24

### Added

- Initial independently versioned environments release candidate.
