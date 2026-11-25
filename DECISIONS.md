# Decisions

Settled by the owner. Agents do not reopen these; propose a new decision in an issue instead.
Requirement IDs refer to [SPEC.md](SPEC.md).

D3–D5 record the historical swarm setup. The four roles are off, and the
provenance and swarm checks are optional on `dev`. [Current dev checks](MAINTAINING.md#checks-on-dev)

| ID | Date | Decision |
|---|---|---|
| D1 | 2026-11-24 | One repo with three packages (`engine`, `components`, `environments`) plus `contracts/`; all work goes to `dev`. |
| D2 | 2026-11-24 | Locked to 0.3. Runs saved with v0.2 stay frozen and read-only and are never reopened by the current engine (R4). |
| D3 | 2026-11-24 | The swarm, author, merger and issue agent run as four host processes. The independent reviewer runs as a separate model call from the Swarm process (R19). GitHub's native merge queue is unavailable on a personal account, so the merger agent serialises merges itself. |
| D4 | 2026-11-24 | Codex runs development roles using gpt-6.2-sol and gpt-6.2-astra. Claude acts as orchestrator and independent reviewer. |
| D5 | 2026-11-24 | The issue agent assigns one size. Small issues use Sol medium. Large and unlabelled issues use Astra medium unless the issue specifies another effort. Reviewer priority is Claude Opus 5.5 low, Astra medium, then Sol medium, subject to eligibility. Sol-authored PRs may use Opus or Astra. Astra-authored PRs require Opus. Sol never reviews and self-review is forbidden. Retry quota, rate-limit and authentication failures with bounded backoff before trying an eligible fallback. Without one, label the PR `awaiting-review`, skip it in the merge queue and retry each loop. Record effort, model and fallback reason with the exact-head verdict (R19). |
