# Engine architecture

A business simulation needs three kinds of state. The **controller checkpoint** records the queue, clock, staff bookings and company state. **Native applications** own their documents, permissions and versions. **Observations** record what a role or an independent collector could see at a particular time. Keeping these distinct lets you ask whether a decision made sense with the evidence available then.

## Follow one event

1. The host opens a directory with a company handler, seed and configuration. An opening batch adds roles and events atomically. An exact replay of the batch reuses it.
2. SQLite orders events by requested minute, priority and insertion order. The scheduler admits staffed work into an available shift. A duration-bearing job has a start and a completion; the company handler runs at completion and can inspect the actual start.
3. The handler interprets the payload. It can read scoped evidence through `run.observe(role, as_of=run.event_started_at)`, apply a deterministic rule or call a provider. The event's role constrains observation. Native account commissioning and business authority belong to the host, adapter and application.
4. The handler can modify JSON state, enqueue follow-up work or request a native write. A native write journals intent, resolves a stable operation ID, sends the request and reads the stored record back. A native application can reject it. Recovery depends on reliable lookup or idempotency at that application.
5. A successful completion commits state and follow-up events together. Reopening the same run continues from its checkpoint. For external writes, partial progress can already be visible in the other application; the journal allows it to be reconciled.
6. An owner can inspect journaled evidence. In the manufacturing application, separately credentialed readers collect snapshots into Delta. A missed intermediate version can remain unknown. The engine itself does not create a data lake for every company.

| Component | Implementation |
|---|---|
| Queue, clock, observation, dispatch and recovery | [Engine](../packages/engine/src/engine/__init__.py) |
| Append-only catalog validation | [Extension validation](../packages/engine/src/engine/_extension.py) |
| Attempts, provider responses, readbacks, metrics and viewer | [Journal](../packages/engine/src/engine/_journal.py) |
| Seeded external processes and role-visible notices | [World](../packages/engine/src/engine/world.py) |
| Company-specific behavior and grading | [Consumers](../packages/environments/src/environments/consumers) |
| Native manufacturing authority and scheduled collection | [Company integrations](../packages/environments/src/environments/norvale/integrations/company_run) |

## What can be replayed?

**Resume** keeps the same company, committed operations and business history. **A policy comparison** needs separately isolated runs with comparable opening state, external events and software versions, plus a checker for the outcome. A seed alone cannot reproduce a live provider response or roll back an external ERP database. Turnkey counterfactual replay of any historical enterprise and automatic discovery of its ontology remain research goals.
