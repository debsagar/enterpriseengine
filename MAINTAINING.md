# From a company requirement to a released engine

EnterpriseEngine owns reusable simulation behavior and the installable Python package. Norvale owns the deployed company, software mappings, scenarios and frozen datasets. EnterpriseKnowledge indexes the research and decisions that led here.

Start a company behavior change by identifying the engine capability it relies on. If that contract changes, implement and test it here first. Exercise shared behavior with both consumers, build the wheel, install it into a clean environment and run the public example. Update the package version and publish an immutable matching Git tag. A pure scenario change can use an existing published contract.

Then update Norvale's engine lock to the tested commit, carry the corresponding runtime snapshot, implement the company adaptation and exercise its normal entry point. An already commissioned company records its engine version and keeps its own checkpoint. Before an upgrade, identify the last working company revision, data snapshot and destination service versions; test compatibility on a copy. Reverting code alone cannot reverse a submitted ERP record or database migration.

- [Norvale runtime and dataset repository](https://github.com/debsagar/norvale)
- [Historical research index](https://github.com/debsagar/enterpriseknowledge/blob/main/INDEX.md)

## Agent tasks on dev

Assign one `agent-ready` issue per worktree based on `origin/dev`. Record the
goal, acceptance command, affected packages and dependencies in the issue.
Open the pull request against `dev`, include the test result and compatibility
notes, then request review from a different model. A human merges after review
and the required checks pass.

Install the workspace with `uv sync --locked --all-packages`. Run the engine
tests in `packages/engine/tests` and the cross-package replay tests in
`contracts`. The package workflow lists the offline commands and also checks
import direction and installed-wheel compatibility.

Use `uv run norvale-generate` and `uv run norvale-simulate` from the workspace.
See the [Norvale guide](docs/NORVALE.md) for prerequisites and the
[release notes](docs/RELEASE_NOTES.md) for the wheel gate.
