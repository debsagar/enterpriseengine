---
name: run-180
description: Shared work loop for every agent (Claude, Codex, Gemini) preparing Norvale's 180-day data-generation run. Use when picking up, doing or reporting any run-180 fix.
---

# run-180

Goal: Norvale can run 180 business days live and produce authentic records. Plan with evidence: https://claude.ai/artifact/Bo23EzProH7BJdARr8ZccM

## Tracker
GitHub issues with label `run-180` are the only task list:
`gh issue list -R debsagar/enterpriseengine -l run-180`
Each issue says what to do and when it is done. Take one that is open and not `in-progress`, add `in-progress` and comment your agent name. Don't take an issue someone else has.

## Loop (one issue at a time)
1. `git fetch origin`, then `git worktree add /data/enterprise/.worktrees/<issue>-<slug> -b fix/<issue>-<slug> origin/dev`. Never work in the main checkout.
2. Make the smallest fix. Reuse existing code. No new frameworks.
3. Check: `ruff check`, `ruff format --check`, the offline tests named in README.md, and a test that fails without your fix.
4. Open a PR to `dev` with `Closes #<issue>` and say what you ran. Merge when the required checks pass (`gh pr merge --squash --delete-branch`). Add a CHANGELOG Unreleased line if package source changed.
5. Comment the result on the issue: what changed, what you ran, and what is still unverified. Remove the worktree.

## Rules
- Paid model calls and live probes need the owner's go, except a probe the owner already approved.
- Never print secrets. Everything stays under /data.
- Claims cite a file:line or a trace line. If you are unsure, say so.
- Behaviour (prompts, evidence bundle, model choice) issues are labelled `behaviour`. Software agents don't change them.
- If blocked, comment on the issue with the reason, add label `blocked`, and move on.
