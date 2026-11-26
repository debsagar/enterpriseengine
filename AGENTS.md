# Instructions for coding agents

Read [SPEC.md](SPEC.md) and [DECISIONS.md](DECISIONS.md) before any work. Every issue and PR cites a requirement ID (R1–R20).

Read README.md → docs/HANDOFF.md → docs/ARCHITECTURE.md before changing behavior. Read docs/PROVENANCE.md before making claims about the delivered dataset. These instructions apply to this repository; the user's current task takes precedence.

- The objective is a coherent enterprise generating records in real, independently stored applications. Preserve the A–L operating loop; map each change to its blocks, evidence inputs, resulting records and a meaningful check.
- Extend the existing engine; do not create a second simulation loop, invent substitute application tables, or narrow a whole-company task to a canned single transaction.
- JEV routes cases; LLMs judge consequential cases; code handles routine work. Preserve role-scoped evidence, authority checks, explicit failures and readback. Hidden regimes and future schedules are private.
- Do not describe ERP modules as separate MES/CRM applications. They remain missing. Do not claim policy reuse or benchmark quality that the saved results do not show.
- The delivered data is frozen evidence. Preserve hashes and past failures/corrections. New behavior belongs in a distinct run.
- No new model-call/spend ceiling without owner instruction; never silently approve on failure. Do not run generation, make paid calls, touch live services, or publish unless the current task authorizes it.
- Never commit credentials, raw SQL, mail archives, private state/traces/oracles, service volumes or runs/. Public exports must retain useful native links but exclude privileged simulation hints.
- Keep changes simple: reuse native product features and existing clients. Build reusable, company-agnostic components that companies (Norvale first) are assembled from (owner, 2026-11-25); no abstraction beyond what a component needs, no mandatory nested routers.
- Notebook inspection stays read-only. Explain generated tables directly underneath their outputs. No unnecessary ZIP deliverables.
- Run `ruff check` and `ruff format --check` before pushing.
- Run appropriate offline checks from README.md. Report what was actually checked, not just saved historical pass results.
- Project state lives in PROJECT-LOG.jsonl (append-only). Read relevant recent events. Record validation evidence in the PR. The four swarm roles are off, and the provenance and swarm checks are optional on dev. Maintainers own updates to the shared log.

For code changes, completion notes should name affected A–L blocks, changed behavior, validation and remaining limits. Update architecture/docs only where the implementation or intended contract changed.
- 180-day run work: follow `skills/run-180/SKILL.md` (shared by all agents; tracker = GitHub issues labelled `run-180`).
