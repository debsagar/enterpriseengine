# Changelog

## [Unreleased]

### Added

- Add `Engine.commit_component_state()` to commit one registered component's state mid-handler without releasing the event's other edits.
- Add `Engine.fork()` to branch a run at a committed minute, copying engine and registered component state.
- Lock checkpoint and dataset compatibility to package and schema versions.
- Widen `Engine.private_truth()` with `entities`, `links`, `cases` and `decisions` records through the cutoff, keeping the existing world, delivery, marker, answer and obligation keys.

### Changed

- `Engine.fork()` documents its snapshot hook contract and rejects a hook that alters the copied engine checkpoint.
- Correct links and offline test commands in the engine README.

## [0.3.0] - 2026-11-24

### Added

- Initial independently versioned engine release candidate.
