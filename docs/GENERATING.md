# Generate a new native run

Inspection of the bundled data needs only Python 3. Generation additionally needs a Linux Docker host, Docker Compose, OpenSSL, network access to the model providers, and sufficient memory/disk for ERPNext and its supporting services. Python generation code uses the standard library.

The bootstrap is for an isolated development stack: it inherits fixed `admin` ERP/database bootstrap passwords. Keep services on their Docker network; do not expose this stack to the internet. Calendar and Matrix are provisioning dependencies; the delivered business evidence focuses on ERP, Gitea, and mail.

## Prepare images and the shared clock

From the repository root:

```sh
docker build -t nv11-erp:15.124.1 -f norvale/environment/images/Dockerfile.erp .
docker build -t nv11-calendar:3.9.0 -f norvale/environment/images/Dockerfile.calendar .
mkdir -p env-cache/lib
clock_container=$(docker create nv11-erp:15.124.1)
docker cp "$clock_container:/usr/lib/x86_64-linux-gnu/faketime/libfaketime.so.1" env-cache/lib/
docker rm "$clock_container"
```

The library must match the host/container architecture. `NORVALE_CLOCK_LIB` can point to another directory containing `libfaketime.so.1`. Remaining service images are pinned in `norvale/environment/docker.py`; Gitea is pinned in `native/provision_tickets_v2.py`.

## Configure model access

Export `OPENROUTER_API_KEY` and `TYPESAFE_API_KEY` in your shell. Optional `NORVALE_MODEL` defaults to `openai/gpt-4.1-mini` for OpenRouter. Alternatively set `OPENAI_API_KEY`, with optional `OPENAI_BASE_URL` and an appropriate `NORVALE_MODEL`. `.env.example` lists variable names; files are not automatically loaded. No API keys are shipped and no machine-local credential file is consulted.

Generation makes paid network calls. There is no built-in spending ceiling. Missing credentials stop judgment explicitly.

## Provision, attach tickets, then execute

Use a new empty run directory; never point this at the published `data/` snapshot or another existing run.

```sh
python3 generate.py --run "$PWD/runs/my-run" --provision --days 120
python3 native/provision_tickets_v2.py "$PWD/runs/my-run"
python3 generate.py --run "$PWD/runs/my-run"
python3 native/verify_run.py "$PWD/runs/my-run"
```

Provisioning initializes services; the subsequent command advances the modeled company. Resume with the same final generation command. The verifier connects to services and writes new exports; it is not an offline reader. All `runs/` contents are ignored by Git, including credentials, private trace/state, raw SQL and mail archives. Keep them private.

A fresh run is not guaranteed to reproduce the delivered trajectory byte-for-byte: real provider judgments and service behavior vary. This release was checked offline and against saved completed-run evidence; its bootstrap was not re-provisioned during packaging.

## Rebuild inspection artifacts

The HTML record viewer uses only the standard library:

```sh
python3 scripts/build_viewer.py
```

To rebuild the executed notebook over bundled data:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python native/build_behavior_notebook.py --out notebooks
```

Open notebooks from `notebooks/`; their data path is `../data/observable`. Run All only reads files. To inspect a new run, pass `--run runs/my-run/export`; the generated notebook uses a relative path to that export's `observable/` directory. `native/build_evidence.py` separately builds development reference questions from private trace/state of a new run; private traces are not included in this release.
