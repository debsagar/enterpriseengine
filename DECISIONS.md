# Decisions

Settled by the owner. Agents do not reopen these; propose a new decision in an issue instead.
Requirement IDs refer to [SPEC.md](SPEC.md).

| ID | Date | Decision |
|---|---|---|
| D1 | 2026-11-24 | One repo with three packages (`engine`, `components`, `environments`) plus `contracts/`; all work goes to `dev`. |
| D2 | 2026-11-24 | Locked to 0.3. Runs saved with v0.2 stay frozen and read-only and are never reopened by the current engine (R4). |
| D3 | 2026-11-24 | Four agent roles: swarm, author, merger, issue agent (R19). GitHub's native merge queue is unavailable on a personal account, so the merger agent serialises merges itself. |
| D4 | 2026-11-24 | Development agents run on Codex only (gpt-6.2-sol, gpt-6.2-astra). Claude acts only as orchestrator. |
