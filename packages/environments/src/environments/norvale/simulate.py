"""Run one versioned native company scenario from a clean checkout."""

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .native.long_run import PreflightError, preflight
from .paths import CHECKOUT, PACKAGE, RUN_ARCHIVE, SOURCE_LAYOUT

# Withheld from every child process of an offline run.
PROVIDER_SETTINGS = ("TYPESAFE_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "NORVALE_COMMS_MODEL")


def load_scenario(path):
    cfg = json.loads(path.read_text())
    if not isinstance(cfg, dict) or set(cfg) != {"version", "days", "seed", "opening_orders"}:
        raise ValueError("Expected version, days, seed and opening_orders")
    if type(cfg["version"]) is not int or cfg["version"] != 1:
        raise ValueError("Unsupported scenario version")
    if type(cfg["days"]) is not int or not 1 <= cfg["days"] <= 365 or type(cfg["seed"]) is not int:
        raise ValueError("Expected 1..365 days and an integer seed")
    from environments.norvale.walkthrough import EnterpriseSimV4

    catalog = EnterpriseSimV4(seed=cfg["seed"], days=30, mode="dry-run")
    items = {sku["sku_id"] for sku in catalog.skus}
    if not isinstance(cfg["opening_orders"], list):
        raise ValueError("opening_orders must be a list")
    for order in cfg["opening_orders"]:
        if not isinstance(order, dict) or set(order) != {"customer", "item", "quantity", "due_day"}:
            raise ValueError("Invalid opening order fields")
        if order["customer"] not in catalog.accounts or order["item"] not in items:
            raise ValueError("Unknown customer or item in opening order")
        if type(order["quantity"]) is not int or order["quantity"] < 1:
            raise ValueError("Opening quantity must be a positive integer")
        if type(order["due_day"]) is not int or not 0 <= order["due_day"] <= 365:
            raise ValueError("Opening due_day must be an integer from 0 to 365")
    return cfg


def main(*, checkout=CHECKOUT):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scenario", type=Path, default=PACKAGE / "scenarios/norvale-q1.json")
    p.add_argument("--run", type=Path, required=True)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--services", choices=("status", "stop", "start"), help="Operate existing containers for this run"
    )
    mode.add_argument(
        "--plan", action="store_true", help="Validate inputs and print the setup without starting services"
    )
    p.add_argument(
        "--offline",
        action="store_true",
        help="Run the native services with deterministic role decisions: no model calls or provider credentials",
    )
    a = p.parse_args()
    runtime = a.run.resolve()
    mode_name = "offline" if a.offline else "live"
    env = dict(os.environ)
    if a.offline:
        env["NORVALE_OFFLINE"] = "1"
        for key in PROVIDER_SETTINGS:
            env.pop(key, None)
    try:
        if runtime.is_relative_to(checkout) or checkout.is_relative_to(runtime):
            raise ValueError("Choose a private run directory outside the checkout")
        if a.services:
            from environments.norvale.integrations.company_run.services import manage

            launcher_run = (runtime / "scenario.json").is_file() and (runtime / "revision").is_file()
            commissioned = (runtime / "services.json").is_file() and (runtime / "business/config.json").is_file()
            if not (launcher_run or commissioned):
                raise ValueError("Choose an existing simulation run")
            with (runtime / "simulate.lock").open("a") as lock:
                if a.services != "status":
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                rows = manage(runtime, a.services)
                if a.services == "status":
                    print(json.dumps(rows, indent=2))
            return
        cfg = load_scenario(a.scenario)
        if a.plan:
            archive = str(runtime / RUN_ARCHIVE)
            print(json.dumps(dict(scenario=cfg, run=str(runtime), mode=mode_name, archive=archive), indent=2))
            return
        if not a.offline and (
            os.environ.get("NORVALE_OFFLINE") == "1"
            or not os.environ.get("TYPESAFE_API_KEY")
            or not any(os.environ.get(k) for k in ("OPENAI_API_KEY", "OPENROUTER_API_KEY"))
        ):
            raise ValueError("Live mode requires JEV and role-model credentials and NORVALE_OFFLINE unset")
        __import__("deltalake")
        __import__("pyarrow")
        os.umask(0o077)
        release = runtime / "release"
        fresh = not runtime.exists()
        if fresh:
            if not (Path(os.environ.get("NORVALE_CLOCK_LIB", "")) / "libfaketime.so.1").is_file():
                raise ValueError("Set NORVALE_CLOCK_LIB to the directory containing libfaketime.so.1")
            subprocess.run(["docker", "compose", "version"], check=True)
            if not a.offline:
                preflight(runtime)
            runtime.mkdir(mode=0o700)
        elif not (runtime / "scenario.json").is_file():
            raise ValueError("Existing directory is not a commissioned simulation")
        lock = (runtime / "simulate.lock").open("a")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not fresh:
            if not (runtime / "scenario.json").is_file() or load_scenario(runtime / "scenario.json") != cfg:
                raise ValueError("Run already exists with different or missing scenario")
            if not (runtime / "commissioned").is_file():
                log = "private/logs/provision.log" if (runtime / "run.json").is_file() else "provision.log"
                raise ValueError(f"Commissioning was interrupted; inspect {log} before resuming the same services")
            record = runtime / "run.json"
            commissioned_mode = json.loads(record.read_text())["mode"] if record.is_file() else "live"
            if commissioned_mode != mode_name:
                raise ValueError(
                    f"This run was commissioned {commissioned_mode}; do not mix offline and live decisions"
                )
        else:
            revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
            release.mkdir()
            (runtime / "revision").write_text(revision + "\n")
            (runtime / "scenario.json").write_text(json.dumps(cfg, indent=2) + "\n")
            from .verify_layout import start_run

            start_run(runtime, cfg, git_sha=revision, mode=mode_name)
            subprocess.run(["git", "archive", revision, "-o", str(runtime / "source.tar")], cwd=checkout, check=True)
            subprocess.run(["tar", "-xf", str(runtime / "source.tar"), "-C", str(release)], check=True)
            integrations = release / SOURCE_LAYOUT / "integrations"
            shutil.copytree(integrations / "revisions/odoo_addons", integrations / "mes/addons", dirs_exist_ok=True)
            with (runtime / "private/logs/provision.log").open("w") as log:
                subprocess.run(
                    ["uv", "sync", "--locked", "--all-packages", "--python", sys.executable],
                    cwd=release,
                    env={**env, "UV_PROJECT_ENVIRONMENT": str(release / ".venv")},
                    check=True,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                subprocess.run(
                    ["bash", integrations / "company_run/provision.sh", str(runtime)],
                    cwd=release,
                    env={**env, "NORVALE_COMPANY_OPERATING": "1"},
                    check=True,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            (runtime / "commissioned").touch()
        python = release / ".venv/bin/python" if (release / SOURCE_LAYOUT).is_dir() else sys.executable
        # Archives made before the entry points still carry the root launcher.
        generator = ["generate.py"] if (release / "generate.py").is_file() else ["-m", "environments.norvale.generate"]
        # Runs commissioned before the observable/private layout have no run.json and stay as they are.
        layout = (runtime / "run.json").is_file()
        if layout:
            from .verify_layout import finish_run, start_run

            start_run(runtime, cfg, git_sha=(runtime / "revision").read_text().strip(), mode=mode_name)
        completed = False
        try:
            subprocess.run([python, *generator, "--run", str(runtime / "business")], cwd=release, env=env, check=True)
            completed = True
        finally:
            # An interrupted run still gets a report that says so.
            if layout:
                report = finish_run(runtime, cfg, completed=completed)
        if layout and not report["ok"]:
            raise ValueError(f"Run verification failed; see {runtime / 'verification.json'}")
    except (OSError, ImportError, ValueError, PreflightError, subprocess.SubprocessError) as exc:
        p.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
