# Employee behaviour standard

Status: proposed 2026-11-27, awaiting owner approval. Once approved it gets a row in DECISIONS.md and is not reopened without one.

## Why this exists

Norvale's records should look like a company where people do their jobs. In the 14-day dry run of 2026-11-27, only purchasing made decisions (19). Quality opened 20 non-conformances and closed none, engineering made no design changes, and chat was a fixed daily post written by code. Each role had been wired by hand, so there was no rule for what a role must do or how to add one. This document is that rule. Every role, existing or new, meets it.

## Terms

- **Role**: a job in the company roster (`walkthrough.ROLES`, e.g. `QA_MGR`, `PLANNER`).
- **Case**: one piece of work a role owns, e.g. "non-conformance NC-0012 is open". It has an owner, a due date and an escalation target.
- **Trigger**: a query over native records (ERP, QMS, PLM, MES) that opens a case. Never a calendar slot alone.
- **Action**: one choice from the role's closed list. Each action maps to exactly one native write that is read back.
- **Facts**: values and contradictions computed by code from the evidence before anyone decides (`judgment.facts`).
- **Offline rule**: the deterministic answer used when `NORVALE_OFFLINE=1`, so the free dry run exercises every role.

## The role contract

Every role that acts declares one entry with these fields, in one registry. Adding a role means adding an entry and its offline rule, not a new phase method.

| Field | Rule |
|---|---|
| `role` | A roster `role_id`. |
| `owns` | The native record types this role is responsible for. |
| `trigger` | A native-record query. It is checked every business day; each matching record opens at most one case (key = role + record + version). |
| `evidence` | Only records this role's account may read, as of the case date. No private truth, no future schedule. |
| `facts` | Code-computed checks, including the contradictions that must block an approval. |
| `actions` | Closed list, 2–4 items, at least one that is not approval. Each names its native write. |
| `offline_rule` | Deterministic choice from facts. Must not approve everything: it follows the facts. |
| `due_days`, `escalates_to` | A case past due without an action is escalated to this role as a new case. |
| `messages` | What the role tells whom after an action, linked to the record ID. Drafted by the small model, checked by `judgment.check_draft`. |

## How a case is decided

1. The trigger opens the case.
2. If an effective standing policy covers it, the policy decides (code, no model).
3. Otherwise JEV routes it: routine goes to the offline rule, a judgment case goes to the decision model with the role card, evidence, facts and policies (`judgment.prompt`).
4. `judgment.graded` holds any approval that is over the limit, has no cited evidence or ignores a flag.
5. The chosen action makes its native write and reads it back. A failed write leaves the case open for retry; it never counts as decided.
6. Messages follow the action. No message is sent without a case behind it.

## Run-level targets

These are pass/fail, computed into `behaviour.json` at the end of every run, including the free dry run.

| Target | Pass when |
|---|---|
| Every role acts | Each role with a matching trigger record makes at least one decision per 5 business days. |
| No silent backlog | No case is past due without an action or escalation. |
| Cases close | For each owned record type, closed ÷ opened ≥ 0.5 after the first 5 days. |
| Not a rubber stamp | Approval rate below 100% for every role with ≥10 decisions; zero approvals over a code flag without a cited resolution. |
| Messages are grounded | Every chat or mail message links to a case and a record. |
| Software unaffected | `verification.json` passes and the sell, buy, make and design chains complete. |

## What each test proves

- The free run (`NORVALE_OFFLINE=1`, no model) proves wiring only: every trigger opens cases, every offline rule's action makes its native write, and the targets above are computed. It says nothing about decision quality.
- A run must be 14 days or longer to count. The buy chain first completes on day 7, so a 5-day run fails verification for that reason alone.
- Decision quality comes only from a run on the decision model (JEV, with the owner's spend ceiling), checked against the targets and the labelled decision test set (#87).

## Expected cases per role (for cost)

Measured in the 14-day free run `/data/runs/dry-14d-20261127` (2026-11-27), before any new role existed. These counts are the source records each trigger would see, so they bound how many cases a role opens.

| Role | Source records in 14 days | Cases per business day |
|---|---|---|
| `SC_MGR` | 19 purchase and reorder decisions (per day: 0,2,5,0,3,0,1,1,0,1,3,1,0,2) | about 1.4 |
| `QA_MGR` | 20 non-conformances opened | about 1.4, plus re-checks of cases kept open |
| Finance | 11 supplier invoices | about 0.8 |
| `PLANNER` | shortage tasks, a handful | under 0.5 |
| `PROC_ENG` | items with 2 or more non-conformances | under 0.5 |

That is about 4–5 cases a day in total. Only cases JEV classes as judgment calls reach the decision model, so model calls per day are at most this number; each also costs one small-model draft for its message. Re-measure after the first JEV run.

## Relation to issues B1, B2 and B5

This standard does not replace them. It is the frame they plug into for every role:
- B1 (#85, evidence bundle and code-computed facts) is the `evidence` and `facts` fields.
- B2 (#86, graded actions that execute with citations and role cards) is the `actions` field and decision steps 3–5.
- B5 (#89, small model drafts messages, decision model picks the action) is the `messages` field.
They were written for purchasing only; under this standard, each new role must supply its own facts, actions and messages in the same form.

## Adding a role

1. Write the contract entry, including the offline rule.
2. Add a test that the offline rule picks a non-approval when a blocking fact is present.
3. Run the 14-day free dry run. The new role must meet the targets above.

## First roles

| Role | Trigger | Actions |
|---|---|---|
| `SC_MGR` | Draft PO or reorder change awaiting approval (existing) | approve, approve with condition, request info, reject |
| `QA_MGR` | Non-conformance open and its lot has a final outcome | close, keep open, escalate to `PLANT_MGR` |
| `CFO` / finance | Supplier invoice submitted and unpaid | approve for payment, hold, request info |
| `PLANNER` | Released stock short for a planned work order | wait, reschedule, escalate to `SC_MGR` |
| `PROC_ENG` | Two or more non-conformances on one item within 14 days | raise a PLM design change, no change |

## Basis

The rules follow what the behaviour heartbeat (`/data/enterpriseknowledge/research/heartbeat-agent-design/`) found in published agent designs: code computes the checkable verdicts, roles choose from a closed action set and cite evidence, an override binds only on code-verified evidence, and doing nothing must not pass. The case fields come from `docs/COMPANY-LOGIC.md` §2. Rates, due days and thresholds above are authored assumptions, not calibrated human behaviour.
