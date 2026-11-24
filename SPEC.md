# EnterpriseEngine specification

Version: 1 (approved by the owner, 2026-11-24). Every agent reads this file before working.
Every issue and PR cites at least one requirement ID (R1–R20). Settled questions live in
[DECISIONS.md](DECISIONS.md) and are not reopened by agents.

Tags: **[O]** owner requirement · **[M]** manager requirement · **[P]** agent proposal accepted by the owner.

## 1. Goal

A system that simulates a company realistically enough to answer **what-if questions** and
produce records that look like a real client's databases. First question it must answer:

> The steam supply to tank TK-101 drops during a batch. What happens to that lot, its order and
> the customer, and does it change if the operator pauses to check?

Its data must stand up to comparison with public real-world event logs.

## 2. Three parts

| Part | Owns | Never owns |
|---|---|---|
| **Engine** (`packages/engine`) | Time, event ordering, scheduling and dispatch, save and resume, branching, who-can-see-what as of a time, confirmed writes to business software, recording model calls | Any entity type: roles, people, products, equipment, recipes, policies, demand |
| **Component library** (`packages/components`) | Every entity type and its behaviour, declared with fields, saved state, events handled and emitted, settings (with dated values), required connectors, provenance | Any one company's specifics |
| **Environment** (`packages/environments`) | One company assembled from a component list: sites, people, authority, software used, starting state | Its own copy of component logic |

## 3. Requirements

### Engine
- **R1 [O]** One engine runs every company and contains no entity types. Everything, including roles, is a component.
- **R2 [O]** Settings and policies change during a run without code edits, as dated events made by someone with authority. Every decision records which version of a setting it used.
- **R3 [P]** Branching copies engine state, every component's state and the business software's state, so one branch's writes never appear in another.
- **R4 [O]** Strong versioning locked to 0.3 and later. Saved runs, companies and datasets record engine and component versions; mismatches are refused; old versions never affect a current run.

### Components
- **R5 [O]** The library is broad, not limited to Norvale, and based on real software and equipment where possible.
- **R6 [M]** It covers equipment, products, recipes, decision history, and plant diagnosis as part of the company model.
- **R7 [O]** Each component declares what it produces for others; a check refuses any company whose components don't fit.
- **R8 [O]** A pixel-art web catalog shows every entity type, generated from the library so new components appear automatically.
- **R9 [O]** People act in three modes: routine, escalate, reflect. Reflect stays only if a test shows it differs from ordinary review.

### Environments and pipeline
- **R10 [O]** Pipeline: choose scenario → sample a compatible company → load components → run → extract records.
- **R11 [O]** Norvale is the first company; a second company proves reuse. LLM-generated companies come last.
- **R12 [O]** Factory focus: shop-floor what-if questions with consequences felt elsewhere in the company.

### Truth and validation
- **R13 [O]** Every run's final data is compared against the AutoTwin datasets (Zenodo: synthetic P2P, OCEL order management, ForgeFlow; BPI 2017 and 2019) with a report per run.
- **R14 [O]** Benchmark answers are derived as on a real client database: from records an analyst could see, citing supporting rows. Hidden simulator state is only for debugging the simulator.
- **R15 [M]** The shop-floor slice works alongside Kumo, which sees only evidence the person it helps could see. **[P]** Kumo anomaly flags are leads, not conclusions.
- **R16 [P]** A diagnosis may be "unresolved" when evidence can't distinguish causes. Whole fault episodes and their seeds are held out before data is cut into windows.
- **R17 [O]** After any big change, a 20–30 simulated-day run with no model calls is compared against a saved summary.

### Model visibility
- **R18 [O]** Every model call by a simulated person is recorded: prompt, visible context, tools, output, decision. The owner reviews only `ml-review` work.

### Process
- **R19 [O]** Agents develop the repo under the single `debsagar` GitHub account.
  The owner reviews only model behaviour.
  - **Swarm** (gpt-6.2-sol) tests, validates and files issues. It validates
    up to three PRs concurrently in separate checkouts.
  - **Author** workers write code and open PRs, with up to three active
    claims. Prerequisites must be complete and declared paths or package
    directories must be disjoint from all active claims. Unknown scope
    overlaps all scopes except work confined to `docs/`.
    `size:small` uses gpt-6.2-sol
    at medium effort. `size:large` and unlabelled issues use gpt-6.2-astra
    at medium effort unless the issue specifies another effort.
  - **Reviewer** uses Claude Opus 5.5 at low effort with read-only tools.
    The review includes this specification, the decisions, the issue, the diff
    and acceptance output. The priority order is Opus, gpt-6.2-astra medium,
    then gpt-6.2-sol medium, subject to eligibility. Sol-authored PRs may use
    Opus or Astra. Astra-authored PRs require Opus. Sol never reviews and
    self-review is forbidden. After bounded retries on Claude quota, rate-limit
    or authentication failures, use the next eligible model. If none remains,
    apply `awaiting-review` and retry each loop.
  - **Merger** (gpt-6.2-sol) handles one PR at a time, skips
    `awaiting-review`, and never merges `ml-review`. Required CI and the
    `swarm/validate` and `review/independent` statuses must succeed at the
    current PR head and dev base. Verdict comments bind role, model, effort,
    run ID, fallback reason when applicable, head SHA and base SHA to the
    status and provenance. A base change requires fresh validation.
  - **Issue agent** (gpt-6.2-astra) assigns each issue exactly one size label,
    merges duplicates and records downstream impact, prerequisites and file scope.
- **R20 [O]** One repo, `debsagar/enterpriseengine`. All work goes to `dev` through PRs carrying provenance and citing at least one requirement ID.

## 4. Invariants

- The engine imports no component or environment code.
- Frozen runs, datasets and release tags are never modified.
- What a simulated person can see is separate from hidden simulator state.
- Secrets are never printed or committed.
- No direct pushes to `dev` or `main`; no agent merges its own PR.

## 5. Out of scope for v1.0

- LLM-generated companies.
- Claims about how often incidents occur in real factories (needs real operating data).
- Reopening runs saved before 0.3.
- Live calls to real business systems in automated tests.

## 6. v1.0 finish line

1. The TK-101 two-branch run completes. The difference is traceable from steam supply → sensor readings → operator action → lot → order using only recorded events, and a mid-run restart changes nothing.
2. Norvale and a second company are built from component lists and pass the R7 fit check.
3. Both pass the R17 no-model run, and an R13 comparison report is produced.
4. The R8 catalog lists every registered entity type.
