---
name: run-180
description: Shared work loop for every agent (Claude, Codex, Gemini) preparing Norvale's 180-day data-generation run. Use when picking up, doing or reporting any run-180 fix.
---

# run-180

Goal: Norvale can run 180 business days live and produce authentic records. Plan with evidence: https://claude.ai/artifact/Bo23EzProH7BJdARr8ZccM

## Context: what to read, in this order
Read live sources before documents, and documents in this order. Stop when you have what the task needs.
1. Owner rules: `~/.claude/CLAUDE.md` (Claude) and the owner-preference lines in `/data/enterpriseknowledge/CLAUDE.md` (all agents).
2. Current state: `/data/enterpriseknowledge/HANDOFF.md`. It is the only current-state file. `docs/HANDOFF.md` in this repo is stable onboarding, not current state.
3. What happened since: the last ~30 lines of `/data/enterpriseknowledge/PROJECT-LOG.jsonl`. Not `/data/PROJECT-LOG.jsonl` (VM maintenance) and not this repo's `PROJECT-LOG.jsonl` (stopped 2026-11-24).
4. Closed decisions: `DECISIONS.md`. Requirements: `SPEC.md`. Employee behaviour: `docs/BEHAVIOUR.md`.
5. Live state, never from documents: `gh issue list -l run-180`, `gh pr list -B dev`, `/data/runs/INDEX.tsv` and each run's `business/heartbeat.json`.
6. Research behind a decision: `/data/enterpriseknowledge/research/` (README there indexes it).

Do not take context from `/data/enterpriseknowledge/attic/`, dated files in `handoffs/`, or scratch folders under `/data` (`norvale-*`, `nv-*`, `company-*`). They are history.

## Time rules
- Every fact has a date. A newer owner instruction beats an older document; a closed decision in DECISIONS.md beats both until the owner changes it.
- Compare `HANDOFF.md`'s date with the last log event. If the log is newer, the log says what happened and HANDOFF is stale: say so, and refresh HANDOFF's "Latest state" before you finish.
- A document's claim about a PR, issue or run is only as old as the document. Re-check it live before acting on it or repeating it.
- Write dates as YYYY-MM-DD. "Today", "now" and "latest" mean nothing in a file someone reads next week.
- Finishing a session: append one event to `/data/enterpriseknowledge/PROJECT-LOG.jsonl` and update `HANDOFF.md` "Latest state" with today's date.

## Tracker
GitHub issues with label `run-180` are the only task list:
`gh issue list -R debsagar/enterpriseengine -l run-180`
Each issue says what to do and when it is done. Take one that is open and not `in-progress`, add `in-progress` and comment your agent name. Don't take an issue someone else has.

## Loop (one issue at a time)
1. `git fetch origin`, then `git worktree add /data/enterprise/.worktrees/<issue>-<slug> -b fix/<issue>-<slug> origin/dev`. Never work in the main checkout.
2. Make the smallest fix. Reuse existing code. No new frameworks.
3. Check: `uv run --offline python scripts/check_versions.py --base origin/dev` (fails if package source changed without a CHANGELOG Unreleased line), `ruff check`, `ruff format --check`, the offline tests named in README.md, and a test that fails without your fix.
4. Open a PR to `dev` with `Closes #<issue>` and say what you ran. Wait for CodeRabbit (comment `@coderabbitai review` if it hasn't run) and fix its valid findings. Merge when the required checks pass and no valid CodeRabbit finding is open (`gh pr merge --squash --delete-branch`). Add a CHANGELOG Unreleased line if package source changed.
5. Comment the result on the issue: what changed, what you ran, and what is still unverified. Remove the worktree.

## Rules
- Paid model calls and live probes need the owner's go, except a probe the owner already approved.
- Never print secrets. Everything stays under /data.
- Claims cite a file:line or a trace line. If you are unsure, say so.
- Behaviour (prompts, evidence bundle, model choice, role triggers) issues are labelled `behaviour` and follow `docs/BEHAVIOUR.md`. Software agents don't change them.
- If blocked, comment on the issue with the reason, add label `blocked`, and move on.
