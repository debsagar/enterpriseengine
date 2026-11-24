# EnterpriseEngine swarm

Run from a checkout containing this script. The runner uses the clone at
`/data/enterprise/enterpriseengine` and creates one worktree per issue at
`/data/enterprise/.worktrees/issue-<N>`. It requires Linux with working Codex
sandboxing, Python 3.10+, authenticated `gh` and Codex CLIs, and a Git identity
with push access to the repository's `swarm/issue-*` branches.

```sh
python3 scripts/swarm/run.py --dry-run --once
python3 scripts/swarm/run.py --once
python3 scripts/swarm/run.py --loop --interval 300 --max-parallel 3
```

Dry runs query GitHub without editing it or creating runner state. Run the
offline integration suite from the repository root:

```sh
uv run --no-project python -m unittest discover -s scripts/swarm/tests -p 'test_v1_swarm.py' -v
```

## Issue and PR lifecycle

- Use the `Dependencies` issue section for `#N`, qualified issue references,
  and GitHub issue or PR URLs. A closed issue or a merged prerequisite PR
  satisfies the reference. An `External:` entry stays with a human.
- A create-only Git push makes the claim exclusive. The empty claim commit
  records the worker and starts the `swarm/issue-<N>` branch from `origin/dev`.
  The issue gets a claim comment and a single label update to `in-progress`.
- The worker runs the issue's acceptance test. The PR includes the worker's
  reported command and filtered output. The independent review is posted as a
  GitHub review comment with an explicit verdict and commit SHA. Review and CI
  both have to pass on the current head to receive `needs-human`. The runner
  checks the three Package release gate jobs plus required checks configured on
  `dev`. It allows two repair rounds after the initial attempt.
- A human merges the PR. The runner then closes the linked issue if GitHub
  left it open because `dev` is a non-default branch. Blocked issues with
  satisfied GitHub prerequisites can become `agent-ready`. Issues blocked by
  the runner itself stay with a human.

Keep `/data/enterprise/.swarm/log.jsonl` and the worktrees across restarts. The
local lock prevents sharing that journal between two live processes. The
remote claim prevents a second host from taking the same issue, but moving an
active claim to a different host requires its journal and outstanding local
work. The runner leaves worktrees in place for inspection.
