# Changelog

## [Unreleased]

### Added

- Add point-in-time lake snapshot manifests and readers for mail, calendar, Matrix messages, and master directory records.
- Run the TK-101 `process.tank` plant on the Norvale engine clock (`environments.consumers.tank`): its `PlantState` is registered with the engine so `Engine.fork()` copies it, and a low alarm becomes a `tank_boundary` event. Enable with a `tank` section in the run config.
- Run TK-101 inside a Norvale company loop, offline: the company's setup registers `process.tank`; the `tank_run`, `tank_boundary`, `tank_operator` and `tank_lot` events go through the company's handler (`norvale.native.company_tank`). A configured role (`tank.role`) applies a named policy (`tank.policies`) to decide pause or continue and the decision keeps its inputs. Each inspected lot writes a QMS inspection with its AI-101 reading, an ERP batch release or hold, and the sales-order shortfall computed by `CompanyERP`, plus one shift message, into offline QMS, ERP and mail doubles; every record carries the causing `engine` event ID.
- Add `Engine.fork_run` to the Norvale company, which forks the engine with a `snapshot_external` hook that copies the offline doubles and refuses when live application stacks are attached. A forked run keeps its source's namespace (`origin_run` in `config.json`).
- Bind saved Norvale runs and incremental manifests to package versions.

### Changed

- Run Norvale's daily phases as ordered events on the engine's queue; the controller's state now lives in the engine checkpoint, and `state.json` is read once to seed a pre-engine run and never written.
- Correct source paths and commands in the Norvale integration guides.

### Fixed

- TK-101 no longer rebuilds its full-horizon scan input lists on every plant advance, so the time per simulated day no longer grows with the configured horizon.

## [0.1.0] - 2026-11-24

### Added

- Initial independently versioned environments release candidate.
