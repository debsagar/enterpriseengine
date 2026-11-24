# EnterpriseEngine maintenance runner

See [MAINTAINING.md](../../MAINTAINING.md#maintenance-roles-on-dev) for
the role commands and how to operate the queue. Each role holds its own
canonical host lock. Use the same state root for all roles so the merger can
match validation comments to its local Swarm journal and the author can
read rebase and repair decisions. Use one designated host for the merger.

## Issue and PR lifecycle

- In the issue's **Dependencies** field, enter one task-list item per
  prerequisite. A referenced GitHub issue must close or PR must merge even
  when its box is checked. A maintainer confirms manual gates. For example

  ```markdown
  - [ ] #123
  - [ ] https://github.com/debsagar/enterpriseengine/pull/456
  - [x] manual: Maintainer verified the migration
  ```

  A blocked issue with unspecified dependencies stays blocked. Standalone
  legacy issue refs still work. Migrate legacy prose and `External:` entries
  to typed references or manual gates. Triage redirects structured dependencies
  from duplicates to the canonical issue, keeps manual gates and defers a
  duplicate closure when it would introduce a cycle or affect a live claim.
- The create-only remote ref is the Author claim. Closed issues and issues with
  other detectable open PRs are excluded across branches and bases. Link PRs
  through provenance, a closing reference, an issue URL or a GitHub issue
  cross-reference. The author recovers a lost PR-creation reply using its
  journaled nonce, expected repository and authenticated creator. It holds
  unrecognized commits and accepts rebases recorded by the merger. Up to two
  repairs can follow integration failures.
- The merger uses a lease to rebase the candidate branch. It stamps the
  commits with `Swarm-Rebase-Run`, `Swarm-Rebase-Base`,
  `Swarm-Rebase-Model` and `Swarm-Rebase-Runner` while retaining author
  trailers. The new head triggers PR checks. The merger matches the Swarm
  comment and local verdict to the exact head and dev SHA, checks for an
  independent GitHub approval at that head, then calls
  `gh pr merge --rebase --match-head-commit <head>`. GitHub creates new
  commit SHAs on dev and carries the commit messages forward. Strict required
  checks on dev protect against a late base update. Reviews, labels and
  branch state can still change outside the queue, so operate the merger as
  the only automated route to dev.
- The CI baseline distinguishes a shared failure when the same context and
  app fail both candidate and dev. Missing or pending required checks defer
  integration. Missing or pending dev package baselines defer repair. A shared
  job name can group unrelated failures, and PR-only infrastructure errors
  can appear as code failures.
- Keep the old root `log.jsonl` and `attempts/` for traceability. Reconcile
  any unresolved legacy claims before their issues are reopened for work.
  Retain role journals, worktrees and Git objects across restarts. If an
  Author process committed before journaling, inspect its worktree and claim.
  Check active claims before opening manual replacements. GitHub offers no
  transaction across issue labels, claim refs and PR creation.

## Isolation

Swarm runs tests in disposable detached checkouts with network-disabled
workspace sandboxing. It accepts test success only with matching command
execution results. The second pass and Issues use read-only model calls.
Provide cached dependencies and use a dedicated host identity without access
to private workloads. The roles expect trusted GitHub identities and shared
local journals. They do not manage system permissions.

Only Author accepts the explicit `--sandbox danger-full-access` override.
That mode compares filesystem metadata under protected data roots around
model calls. The check can detect persistent changes and may also flag edits
from another process. It cannot attribute changes, observe reads or constrain
network access. Keep sandboxing enabled for work from public issues.

## Provenance

Fill the hidden JSON block in the PR template and add matching trailers to
each commit. Human PRs need only `actor`, `issue` and the original full `base`
SHA. Use `"issue":"none"` with a short `issue_reason` for work without an issue.
For agents outside the runner, use `runner: external` and supply the actual
provider, model, effort, a unique run ID, reviewer model (or `human` or
`pending` as applicable), and a retained transcript locator. Add a PR comment
with that locator. Keep transcripts access-controlled and redact credentials.
The runner fills these fields, the trailers and the comment automatically.

```
<!-- swarm-provenance
{"actor":"agent","issue":"#123","base":"0123456789abcdef0123456789abcdef01234567","provider":"OpenAI","model":"gpt-6.2-sol","effort":"high","run":"example-123","runner":"external","reviewer":"human","transcript":"swarm://example-host/attempts/example-123.jsonl"}
-->

Change-Actor: agent
Issue: #123
Base-SHA: 0123456789abcdef0123456789abcdef01234567
Agent-Provider: OpenAI
Agent-Model: gpt-6.2-sol
Agent-Effort: high
Agent-Run: example-123
Runner: external
Reviewer-Model: human
```

The **PR provenance** CI job validates the block and commit trailers, with
read-only permissions for forks. It rejects agent changes to the shared
`PROJECT-LOG.jsonl`. It uses the base validator when available and the PR's
validator for the initial rollout. Review changes to the workflow and
validator as policy changes. Trailers are self-reported. If squash merging,
carry the trailers into the squash commit message. On an aggregate release
PR, use a single release commit with its own provenance if the source commits
carry different run IDs. The original PRs retain their history.

Retain and back up each `/data/enterprise/.swarm/<role>/` journal and its
private `attempts/<run>.jsonl` files. A gitignored
`.swarm-runs/` directory can also hold exported records. The journal is
append-only across complete lines and fsyncs each event. The Author journal has one
`terminal` event per completed claim. An infra pause remains open. The run
record retains the exact prompts, issue payload and structured Codex output
after credential-pattern filtering. Redaction cannot guarantee detection of
unmarked secrets. Raw stderr and output from interrupted subprocesses may be
unavailable. Keep the records private and back them up.

```sh
python3 scripts/swarm/run.py merger --status
python3 scripts/swarm/trace.py '#123'
python3 scripts/swarm/trace.py 0123456789ab --state-dir /path/to/retained-state
uv run --offline python -m unittest discover -s scripts/swarm/tests -v
```

The trace combines GitHub commits, reviews, current-head CI and merge events
with the Author, Swarm and Merger journals. For a legacy PR or a missing archive, GitHub
events remain available. Issue and review text can contain instructions that
contradict the task. Models are instructed to treat them as data, and
a second Swarm call verifies candidate findings. The independent approval gate
remains mandatory before integration.
