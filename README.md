# Norvale — Phase 1 data generation v2

Norvale is a synthetic consumer-care manufacturer whose people perform modeled business work in real software: ERPNext, Gitea Issues, and SMTP/IMAP email. This repository contains the v2 generator and the filtered records from its completed 120-day run, January 1–April 30, 2027.

**Helping with development?** Read [the collaborator handoff](HANDOFF.md) and [the operating-loop diagram and code map](docs/ARCHITECTURE.md). Claude and Codex share [AGENTS.md](AGENTS.md).

**Start with the data.** No Docker, API key, or running service is needed to inspect the delivered run.

- Download/open [the interactive record viewer](docs/data-viewer.html) in a browser: select an order and follow work, shipment, invoice, and payment records; switch to tickets or email.
- Read [the executed notebook](notebooks/Behavior.ipynb), or download/open [its HTML](notebooks/Behavior.html).
- Find the complete filtered records in [data/observable](data/observable), with [file hashes](data/manifest.json), [run summary](data/summary.json), and [recorded checks](data/checks.json).
- See [the GitHub-rendered operating loop](docs/ARCHITECTURE.md) and [generation instructions](docs/GENERATING.md).

GitHub displays HTML source; download the repository and open HTML files locally for interaction. The separate [public presentation](https://norvale-company-v2.aswanth-krshna.chatgpt.site) is also available.

## Run the native company

Validate the scenario and inspect the opening orders without starting any services:

```sh
python3 simulate.py --scenario scenarios/norvale-q1.json --run /absolute/path/to/q1 --plan
```

The starter scenario covers January 1 through March 31, 2027. Four submitted customer orders (720 units) enter the ordinary planning queue before day-one demand. Change the seed, dates relative to opening day, customers, SKUs or quantities in the versioned JSON.

For a live run, install the [runtime prerequisites](docs/GENERATING.md), including cached service images, the shared clock library and [Delta dependencies](integrations/lake/requirements.txt). Set `NORVALE_CLOCK_LIB`, `TYPESAFE_API_KEY` and either `OPENAI_API_KEY` or `OPENROUTER_API_KEY`. Remove `--plan` to provision **one** private stack, seed ERPNext, and run the ordinary company with InvenTree PLM, Odoo MES, separate QMS, scheduled Delta ingestion and live decision routing. This command creates containers and can incur provider charges.

Use the same command and run directory to resume after successful commissioning. The directory holds a private release snapshot and Git revision, scenario, business checkpoint, source settings, and lake archive. Keep it outside version control. If commissioning fails, inspect that run’s `provision.log` and service logs before retrying; the launcher refuses to allocate another stack in its place. Use a release tag or commit for future runs and the original pinned source for an existing run. The published 120-day dataset came from the earlier ERPNext, tickets and mail generator. A new live Q1 run is still subject to native commissioning and verification.

## What was generated

| Record | Count |
| --- | ---: |
| Sales orders | 258 |
| Work orders / job cards | 328 / 328 |
| Delivery notes / sales invoices | 321 / 321 |
| Payment entries | 405 |
| Purchase orders | 98 |
| Business maintenance issues | 15 |
| Delivered message records | 119 |

Sales records customer demand; planners authorize work; production posts job-card labor and output; QA releases stock; warehouse ships; finance bills and records payment. Buyers request supply-chain approval; maintenance uses a separate issue service.

These are synthetic activities posted into actual applications. They are not empirical observations of a real company. At cutoff, 50,016 units had been ordered, 47,148 produced, and 46,548 shipped. Open demand is retained.

## Repository map

| Path | Purpose |
| --- | --- |
| `generate.py`, `native/` | Native generation, integrations, routing, validation, artifact builders |
| `norvale/` | Required service provisioning and client support, included locally |
| `walkthrough/engine.py` | Inherited catalog and 19-role definitions used by v2; not the old walkthrough |
| `data/observable/` | Filtered native business records intended for inspection |
| `data/{summary,checks,config,manifest}.json` | Recorded run results, checks, configuration and integrity hashes |
| `docs/`, `notebooks/` | Human entry points and read-only inspection |
| `scripts/` | Offline viewer/data verification |

## Data boundaries

ERPNext stores records in **MariaDB**. Gitea Issues uses its own **SQLite** database. Email lives in **mail files** and is exchanged through SMTP/IMAP. JSON exports in this repository are snapshots of those separate systems; they are not a combined application database or a restorable service backup.

`native-records.json` maps doctype → native ID → document, including child rows. Stock and general-ledger exports are separate. `tickets.json` and `mail.json` contain application readbacks. Synthetic people and `.test` email addresses are intentional business data. Native IDs and cross-system references remain available for joins.

Raw ERP SQL, authentication fields, service tokens, mail archives, Docker volumes, private controller state, and execution traces are excluded. Historical v1 runs, smoke trajectories, local checkpoints, and unrelated projects are excluded too. The full original local evidence remains preserved separately.

## Validation and limits

Run the offline checks from the repository root:

```sh
python3 scripts/check_data.py
python3 native/check_core.py
python3 native/check_routing.py
python3 native/check_retry.py
python3 generate.py --help
```

The completed run recorded nine passing checks, including native links/labor, material and shipment conservation, payment allocations, role permissions, resume behavior, and ledger balance. Those results are saved evidence; the offline checks above do not rerun live-service verification.

This is a generation prototype, not a completed extraction benchmark. Regimes and labor are modeled; QA readings are nominal; payroll is aggregate. Production may run on weekends. Through February 3, policy-unmatched cases incorrectly bypassed JEV; this was corrected February 4 without rewriting history. JEV covers selected role decisions, not every operational API. The run recorded 70 JEV calls and 105 model decisions, with no standing-policy executions. See [limitations and provenance](docs/PROVENANCE.md).

Optional agent interview exercise: [Expert inbox and evidence-based grading](docs/EXPERT-INBOX.md).
