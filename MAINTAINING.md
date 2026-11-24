# From a company requirement to a released engine

EnterpriseEngine owns reusable simulation behavior and the installable Python package. Norvale owns the deployed company, software mappings, scenarios and frozen datasets. EnterpriseKnowledge indexes the research and decisions that led here.

Start a company behavior change by identifying the engine capability it relies on. If that contract changes, implement and test it here first. Exercise shared behavior with both consumers, build the wheel, install it into a clean environment and run the public example. Update the package version and publish an immutable matching Git tag. A pure scenario change can use an existing published contract.

Then update Norvale's engine lock to the tested commit, carry the corresponding runtime snapshot, implement the company adaptation and exercise its normal entry point. An already commissioned company records its engine version and keeps its own checkpoint. Before an upgrade, identify the last working company revision, data snapshot and destination service versions; test compatibility on a copy. Reverting code alone cannot reverse a submitted ERP record or database migration.

- [Norvale runtime and dataset repository](https://github.com/debsagar/norvale)
- [Historical research index](https://github.com/debsagar/enterpriseknowledge/blob/main/INDEX.md)

## Maintenance roles on dev

| Subcommand | Model | Responsibility |
| --- | --- | --- |
| `swarm` | `gpt-6.2-sol` | Runs offline dev checks, PR acceptance tests, and the Norvale #39 baseline when tracked. Checks PRs against requirements and verifies findings in a fresh pass before posting a verdict. Files or updates findings on issues and comments on PRs. |
| `author` | `gpt-6.2-astra` | Claims eligible issues and opens or repairs one `dev` PR per issue cluster. May delegate scoped work in its own sandbox. Owns source edits and PR creation. |
| `merger` | `gpt-6.2-sol` | Applies deterministic integration gates to one PR at a time and summarizes completed merges. Rebases onto current `dev`, adds provenance trailers, and waits for CI, Swarm validation, and trusted independent approval before rebase-merging. |
| `issues` | `gpt-6.2-astra` | Classifies matches, similar issues and distinct issues, closes confirmed duplicates with reciprocal links, and records downstream impact and structured prerequisites. |

The human orchestrator owns the processes. On the designated Linux host,
authenticate `gh` for each role, warm the locked offline workspace cache, and
use a dedicated runner checkout separate from the task clone at
`/data/enterprise/enterpriseengine`. Use distinct GitHub identities for the
author, validator, merger, and independent reviewer. Give the merger write
access to candidate branches and permission to rebase-merge on `dev`.
Enable GitHub rebase merging and strict required checks on `dev`, and disable
GitHub auto-merge for the serial queue.

Set up the runner source once, then pin the reviewed dev SHA in each role's
terminal.

```sh
git clone --branch dev https://github.com/debsagar/enterpriseengine.git /data/enterprise/swarm-runner
cd /data/enterprise/swarm-runner
git fetch origin dev
runner_sha=$(git rev-parse origin/dev)
git switch --detach "$runner_sha"
```

In four separate terminals, set `runner_sha=$(git -C /data/enterprise/swarm-runner rev-parse HEAD)`
and start one foreground command per terminal.

```sh
python3 /data/enterprise/swarm-runner/scripts/swarm/run.py swarm  --loop --runner-sha "$runner_sha"
python3 /data/enterprise/swarm-runner/scripts/swarm/run.py author --loop --runner-sha "$runner_sha"
python3 /data/enterprise/swarm-runner/scripts/swarm/run.py merger --loop --runner-sha "$runner_sha"
python3 /data/enterprise/swarm-runner/scripts/swarm/run.py issues --loop --runner-sha "$runner_sha"
```

Stop a role with Ctrl-C in its terminal. Use `--once --runner-sha "$runner_sha"`
for a single poll, `--once --dry-run` for a read-only preview, or `--status`
to inspect the latest journal entries. Stop all roles before switching the
pinned checkout. Drain or reconcile old Author claims before switching revisions.

The queue is ordered by PR number and retains its active PR across restarts.
For `ml-review` PRs it adds `needs-human` and pauses at that PR. The owner can
complete the manual review and merge, or clear both holds to return it to
automation. Shared CI failures pause the queue. Failed gates and rebase
conflicts receive a comment with the next action. The earlier worker PRs
#35, #37 and #38 enter the same queue. Their authors may need to supply
provenance if the PR provenance check fails. An independent reviewer approves
the freshly rebased head in GitHub after inspecting it. A later change to the
head or base requires fresh evidence. The personal repository uses the local
serial queue. If the repository moves to an organization, the owner can
replace it with GitHub's native merge queue.

Keep role journals under `/data/enterprise/.swarm/<role>/` and review merger
decisions in `merger/history.jsonl` and merge reports in `merger/reports/`.
Use the [runner and provenance guide](scripts/swarm/README.md) for recovery,
dependencies and metadata.

### Workflow references

- Triage compares the underlying problem and acceptance scope, using the match, similar and distinct classifications for related work. [GitHub Agentic Workflows issue triage](https://github.github.com/gh-aw/gallery/ai-issue-triage/), [Microsoft backlog grooming](https://github.com/microsoft/hve-core/blob/main/.github/agents/backlog-grooming.agent.md)
- Swarm limits its GitHub outputs to issues, labels and comments. A fresh validation pass checks PR findings before publication. [GitHub Next Agentics](https://github.com/githubnext/agentics), [Pipecat code review](https://github.com/pipecat-ai/pipecat/blob/main/.claude/skills/code-review/SKILL.md)
- The merger refuses PRs opened by its identity and requires CI on a candidate rebased onto current dev. [Mergify coding-agent merge workflow](https://mergify.com/guides/merge-pull-requests-from-coding-agents)

Install the workspace with `uv sync --locked --all-packages`. Run the engine
tests in `packages/engine/tests` and the cross-package replay tests in
`contracts`. The package workflow lists the offline commands and also checks
import direction and installed-wheel compatibility.

Use `uv run norvale-generate` and `uv run norvale-simulate` from the workspace.
See the [Norvale guide](docs/NORVALE.md) for prerequisites and the
[release notes](docs/RELEASE_NOTES.md) for the wheel gate.
