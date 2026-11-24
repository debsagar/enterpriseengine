# EnterpriseEngine swarm

The runner uses the clone at `/data/enterprise/enterpriseengine` and issue
worktrees at `/data/enterprise/.worktrees/issue-<N>`. It requires Linux,
Python 3.10+, authenticated `gh` and Codex CLIs, and permission to create
`swarm/issue-*` branches and manage issues and PRs. Use a dedicated GitHub
identity for the runner where possible.

## Pin and update the runner

Use a dedicated source checkout and run a reviewed, detached revision from dev.
Keep its source available until its attempts are complete.

```sh
git clone --branch dev https://github.com/debsagar/enterpriseengine.git /data/enterprise/swarm-runner
cd /data/enterprise/swarm-runner
git fetch origin dev
runner_sha=$(git rev-parse origin/dev)
git switch --detach "$runner_sha"
python3 scripts/swarm/run.py --dry-run --once
python3 scripts/swarm/run.py --loop --runner-sha "$runner_sha" --interval 300 --max-parallel 3
```

Updating takes an operator-controlled restart. Inspect `--status` first and
drain open attempts on the previous SHA. Fetch dev, pin and review the new
revision and restart using that revision. The runner checks its source SHA,
cleanliness and reachability from its source clone's `origin/dev`. An attempt
resumes only on its recorded SHA. The source checkout and the task checkout
can advance independently.

The default requires working Codex sandboxing. In `danger-full-access` mode
the runner also compares filesystem metadata under
`/data/enterprise/{runs,datasets}` and both clone and task `data/` trees
around model calls. It checks the issue worktree root, branch, HEAD and changed
paths, and checks that reviewer calls leave it clean. Another process can
change protected data and cause the same detection. Metadata checks cannot
attribute changes, see reads, constrain network or filesystem access, or
reliably detect temporary changes that are reverted. Use sandboxing and a
dedicated OS identity for work from public issues.

## Issue and PR lifecycle

- In the issue form's **Dependencies** field, use one task-list line per
  prerequisite. A GitHub issue must be closed or a PR must be merged even if
  its box is checked. A maintainer records completion of a manual gate by
  checking its box. A manual-only list can unblock after confirmation.
  Empty or unspecified dependencies leave a blocked issue with a maintainer.
  Standalone legacy issue references still work. Legacy prose and `External:`
  entries require migration. For example

  ```markdown
  - [ ] #123
  - [ ] https://github.com/debsagar/enterpriseengine/pull/456
  - [x] manual: Maintainer verified the migration
  ```

- The create-only Git ref update is the claim. Closed issues are excluded.
  Link external PRs via the provenance issue field, a closing reference, a
  GitHub issue URL or a GitHub issue timeline cross-reference. The runner
  checks open PRs across branches and bases before taking or continuing work.
  It acts only on its recorded PR number, with recovery from a lost creation
  reply using a journaled nonce, expected repository and authenticated author.
  GitHub does not provide a transaction spanning issue labels, branch claims
  and PR creation. Maintainers should check for an active claim before opening
  manual replacements. A PR with no detectable issue link may be missed.
- The worker reports acceptance evidence. A separate model reviews the
  current head, then CI must pass before the runner adds `needs-human`.
  A failed check also failing on dev with the same check name and app pauses
  the attempt as `infra-failure`. Pending or missing package baselines defer
  repair. The runner polls again and allows up to two repair rounds for
  PR-specific failures. This compares check identity and result, so unrelated
  failures in the same job can be grouped and environment failures isolated
  to PR jobs can still look like code failures.
- A human reviews and merges. The runner records GitHub's merge timestamp,
  SHA and actor and closes the linked issue if needed. The runner does not
  adopt human-authored commits pushed onto a swarm branch. Repair a released
  or blocked claim with a maintainer-owned PR or explicitly reconcile the
  labels, branch and local journal before retrying.
- Keep the state directory, worktrees and Git objects across restarts. A local
  lock plus the remote ref protect against concurrent runner claims. A restart
  on the same SHA with the journal resumes the existing claim. If a process
  crashed after committing but before journaling the result, the runner holds
  the worktree for operator inspection. A missing journal or a legacy claim
  without this provenance cannot prove ownership. Complete or reconcile such
  work manually before upgrading. Avoid deleting claim refs from hosts still
  working. API failures defer polling; increase the interval or reduce
  parallelism if rate limits persist.

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

Retain and back up `/data/enterprise/.swarm/log.jsonl` and the private
`attempts/<run>.jsonl` files under the same state directory. A gitignored
`.swarm-runs/` directory can also hold exported records. The journal is
append-only across complete lines and fsyncs each event. Watch for one
`terminal` event per completed run. An infra pause remains open. The run
record retains the exact prompts, issue payload and structured Codex output
after credential-pattern filtering. Redaction cannot guarantee detection of
unmarked secrets. Raw stderr and output from interrupted subprocesses may be
unavailable. Keep the records private and back them up.

```sh
python3 scripts/swarm/run.py --status
python3 scripts/swarm/trace.py '#123'
python3 scripts/swarm/trace.py 0123456789ab --state-dir /path/to/retained-state
uv run --offline python -m unittest discover -s scripts/swarm/tests -v
```

The trace combines GitHub commits, reviews, current-head CI and merge events
with the journal for that run. For a legacy PR or a missing archive, GitHub
events remain available. Issue and review text can contain instructions that
contradict the task. Models are instructed to treat them as data, and
separate configured models reduce shared failure modes. Keep a human review
gate for correctness, provenance claims and changes to workflow policy.
