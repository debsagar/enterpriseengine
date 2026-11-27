# Start here: collaborator handoff

This is stable onboarding. Current project state is `/data/enterpriseknowledge/HANDOFF.md`; the reading order is in `skills/run-180/SKILL.md`.

## Goal, in the owner's words

Owner instructions from the project conversation, 2026-11-17:

> “each software generates tables in the real world and merging them is part of the game.”

> “Any and all changes have to be made compatible with the operating loop or we wontbe able to track all the changes”

> “we are working in iterations not one shotting.”

We are building an inspectable synthetic company that acts through real software. Its records will support an extraction harness for an enterprise world model: entities, relationships, history, authority, decisions and supported explanations. Current scope is generation and development references; an evaluated extraction harness is not delivered.

## Latest instruction

> “add the operating loop diagram basically the other collaborator shouldnt drift ... someone with zero context can start helping”

This handoff, agent instructions and GitHub-rendered architecture document implement that request. Start with README.md, then docs/ARCHITECTURE.md, then this file's open work.

## Constraints

- Preserve the published 120-day trajectory and its hashes. Make a separate run for changed behavior; do not repair history to fit expectations.
- No fixed call count or spending ceiling was requested. Keep actual usage/error reporting; never silently substitute approval when a model fails.
- JEV routes routine/known versus judgment-required cases. The LLM makes role-scoped judgments; native software and action checks enforce them. Nested routing is optional, not a required framework.
- Reuse the existing engine. Keep the notebook read-only, focused on business behavior and actual records, with explanations immediately below generated tables.
- Keep independent application IDs and observable crosslinks. Do not merge stores into a fabricated universal source database.
- Secrets, raw service backups and private execution/oracle state are not public or extractor inputs. See docs/GENERATING.md for credential variables and private run locations.
- Do not launch a paid generation run, change shared services or publish without a current task authorizing it. Offline inspection and checks need no services.

## Frozen 120-day dataset

- **Frozen-run software.** ERPNext supplied manufacturing and sales. Gitea handled tickets, and email ran separately. The provisioned Matrix and calendar services produced no business activity. New Norvale runs can use Odoo for manufacturing. [Dataset provenance](PROVENANCE.md) · [Norvale deployment](NORVALE.md)
- **No standing-policy executions occurred.** There were 70 JEV calls and 105 model decisions. Before February 4, unmatched cases bypassed JEV; history is retained. See the [run summary](../data/summary.json) and [`company.py` (`choose`)](../packages/environments/src/environments/norvale/native/company.py).
- **External regimes are authored persistent episodes, not fitted HMMs.** [Regime implementation](../packages/environments/src/environments/norvale/native/regimes.py). Full careers, turnover and organization-wide strategic evolution are not established by this run.
- Four months completed: January 1–April 30, 2027. 258 orders, 328 production jobs and 15 maintenance jobs; 3,468 ordered units remain unshipped. See data/summary.json.
- Nine saved live checks passed; offline release checks do not repeat that live verification. See data/checks.json and scripts/check_data.py.
- Native records and separate ticket/mail exports are bundled in data/observable. These are filtered exports, not restorable full databases. See data/manifest.json.
- The local source project produced 13 development reference questions, but private answers/traces are deliberately absent from this release. [`build_evidence.py`](../packages/environments/src/environments/norvale/native/build_evidence.py) can build them from a new private run. No extraction benchmark score is claimed.

## Running now

No job is launched or managed by this handoff. The delivered trajectory is complete. A clone does not attach to the original VM's services. Before resuming any locally created run, check for an existing controller and read its checkpoint; never start duplicate writers or reset it blindly.

## Promised next / how to contribute

Choose a bounded improvement within the full company objective.

1. Diagnose why reusable policies never executed; demonstrate both routing branches with meaningful evidence, without forcing arbitrary approvals/rejections.
2. Follow the existing Odoo MES ownership and handoffs when extending production. Define record ownership and handoffs before wiring a separate CRM.
3. Expand connected external/internal/organizational evolution. External factors change conditions; actions change wear/workload; authorized decisions change mandates and policies.
4. Strengthen references across easy factual, medium cross-system/temporal, and hard explanation/insufficient-evidence questions. Specify distribution and evidence before multiplying questions. Avoid building a grading framework prematurely.

For any change, follow the A–L change contract in docs/ARCHITECTURE.md, keep the diff focused, run the relevant offline checks, and record the checks in the PR. Maintainers can append shared project events to PROJECT-LOG.jsonl. Use docs/GENERATING.md only when the task calls for a new run.

## Open bugs / unresolved

The frozen trajectory also has these limitations. nominal passing QA, aggregate payroll, modeled daily/shift scheduling, incomplete policy/role evolution, and Gitea controller identity with real server timestamps (simulated dates appear in text). These limit claims, not permission to discard the foundation. Bootstrap uses development credentials and requires isolated networking. Fresh bootstrap was not rerun during release packaging. See docs/PROVENANCE.md and docs/GENERATING.md.

## Pitfalls seen in this project

- Replacing implementation with architecture essays or restarting the company design.
- Treating real application schemas as proof of realistic employee behavior.
- Calling a single-order fixture a full company, or calling a completed run a validated benchmark.
- Exposing hidden factors or private operation keys to roles/the extractor.
- Turning every role into an always-running LLM agent, or arbitrarily sprinkling a few calls into the year.
- Adding a glossary before the notebook when the owner wants explanations beneath its actual outputs.
- Generating unnecessary ZIPs; contributors work directly in their checkout.

## Where details live

- README.md: inspect data and run offline checks.
- docs/ARCHITECTURE.md and docs/operating-loop.mmd: shared loop and code map.
- docs/GENERATING.md: isolated new-run commands and model configuration.
- docs/PROVENANCE.md: trajectory corrections and release limitations.
- Norvale implementation files include [`company.py`](../packages/environments/src/environments/norvale/native/company.py), [`router.py`](../packages/environments/src/environments/norvale/native/router.py), [`regimes.py`](../packages/environments/src/environments/norvale/native/regimes.py), [`systems_v2.py`](../packages/environments/src/environments/norvale/native/systems_v2.py).
- docs/notebooks/Behavior.ipynb: six-cell read-only behavior view.
- PROJECT-LOG.jsonl: append-only evidence history.

The original chat/transcripts and private run evidence are not shipped. This handoff records the necessary intent; do not assume access to the author's VM or reconstruct missing history from guesses.
