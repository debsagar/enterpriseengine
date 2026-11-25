# Changelog

## [Unreleased]

### Added

- Run the TK-101 `process.tank` plant on the Norvale engine clock (`environments.consumers.tank`): its `PlantState` is registered with the engine so `Engine.fork()` copies it, and a low alarm becomes a `tank_boundary` event. Enable with a `tank` section in the run config.
- Bind saved Norvale runs and incremental manifests to package versions.

### Changed

- Run Norvale's daily phases as ordered events on the engine's queue; the controller's state now lives in the engine checkpoint, and `state.json` is read once to seed a pre-engine run and never written.
- Correct source paths and commands in the Norvale integration guides.

## [0.1.0] - 2026-11-24

### Added

- Initial independently versioned environments release candidate.
