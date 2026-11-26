"""Safety for long operating runs: one model endpoint, a spend ceiling, a heartbeat, stalls and preflight.

The owner sets the limits in the environment on every invocation:
NORVALE_SPEND_CEILING_USD (total for the run) and NORVALE_JEV_USD_PER_MTOK (JEV reports tokens, not cost).
A call with no known cost blocks further paid calls until the owner, having allowed for it in the ceiling,
sets NORVALE_UNPRICED_CALLS_RECONCILED to the run's number of such calls.
"""

import json
import math
import os
import shutil
import signal
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

CEILING = "NORVALE_SPEND_CEILING_USD"
JEV_PRICE = "NORVALE_JEV_USD_PER_MTOK"
RECONCILED = "NORVALE_UNPRICED_CALLS_RECONCILED"
# ponytail: one fixed floor; derive it from measured bytes per day once a long run has been measured.
MIN_FREE_BYTES = 20 * 10**9
STALL_MINUTES = 30


class SpendCeilingExceeded(RuntimeError):
    """The owner's spend ceiling is reached, or a paid call cannot be priced under it."""


class PreflightError(RuntimeError):
    """The host or the settings cannot carry a long run."""


class Stalled(BaseException):
    """No trace activity for the stall limit. Not an Exception, so broad handlers cannot swallow it."""


def model_endpoint():
    """Key, base URL and model of one provider. OpenRouter wins when both keys are set."""
    env = os.environ
    if key := env.get("OPENROUTER_API_KEY"):
        return key, "https://openrouter.ai/api/v1", env.get("NORVALE_MODEL", "openai/gpt-4.1-mini")
    if key := env.get("OPENAI_API_KEY"):
        base = env.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        default = "openai/gpt-4.1-mini" if "openrouter" in base else "gpt-4.1-mini"
        return key, base, env.get("NORVALE_MODEL", default)
    return None


def amount(name):
    """An owner-set nonnegative number from the environment, or None when unset."""
    text = os.environ.get(name)
    if not text:
        return None
    value = float(text)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a nonnegative number")
    return value


def spend(state):
    return state.setdefault("spend", {"usd": 0.0, "calls": 0, "unpriced_calls": 0})


def check_spend(state):
    """Call before each paid request. One request can overshoot the ceiling by its own cost."""
    ceiling = amount(CEILING)
    if ceiling is None:
        return
    total = spend(state)
    if total["usd"] >= ceiling:
        raise SpendCeilingExceeded(f"Spend ${total['usd']:.4f} has reached the ${ceiling:.4f} ceiling")
    if total["unpriced_calls"] > (amount(RECONCILED) or 0):
        raise SpendCeilingExceeded(
            f"{total['unpriced_calls']} paid calls have no known cost; allow for them in the ceiling, then set {RECONCILED}"
        )


def record_spend(engine, provider, response):
    """Add a provider response's cost to the checkpoint before its answer is used."""
    usage = (response or {}).get("usage") or {}
    cost = usage.get("cost")
    if cost is None and provider == "jev" and (price := amount(JEV_PRICE)) is not None and usage:
        cost = (usage.get("input_tokens", 0) + usage.get("output_tokens", 0)) * price / 1e6
    total = spend(engine.state)
    total["calls"] += 1
    if cost is None:
        total["unpriced_calls"] += 1
    else:
        total["usd"] += cost
    engine.persist()
    engine.log("spend", provider=provider, cost_usd=cost, total_usd=total["usd"])
    if cost is None and amount(CEILING) is not None:
        raise SpendCeilingExceeded(f"{provider} reported no cost, so the spend ceiling cannot be enforced")


def preflight(run):
    """Refuse to start without owner limits, disk space or a free Docker network."""
    endpoint = model_endpoint()
    if endpoint is None:
        raise PreflightError("Set OPENROUTER_API_KEY")
    if urlsplit(endpoint[1]).hostname != "openrouter.ai":
        raise PreflightError("The spend ceiling needs replies that report usage.cost; set OPENROUTER_API_KEY")
    for name in (CEILING, JEV_PRICE):
        if amount(name) is None:
            raise PreflightError(f"Set {name}; the owner sets it for every long run")

    def docker(*args):
        result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise PreflightError(f"docker {' '.join(args[:2])} failed: {result.stderr.strip()[-300:]}")
        return result.stdout.strip()

    place = Path(run).resolve()
    while not place.exists():
        place = place.parent
    for path in (place, Path(docker("info", "--format", "{{.DockerRootDir}}"))):
        free = shutil.disk_usage(path).free
        if free < MIN_FREE_BYTES:
            raise PreflightError(f"{path} has {free / 1e9:.1f} GB free; need {MIN_FREE_BYTES / 1e9:.0f} GB")
    # Creating a network is the only direct test that Docker still has a free address pool.
    name = f"norvale-preflight-{os.getpid()}"
    docker("network", "create", name)
    docker("network", "rm", name)


def heartbeat(engine, **extra):
    """Replace <run>/heartbeat.json after a stage, and with the reason when the run stops."""
    day = engine.day
    bugs = engine.state.get("company_erp", {}).get("bug_errors", {}).get(str(day), {})
    value = dict(
        written_at=datetime.now(timezone.utc).isoformat(),
        pid=os.getpid(),
        business_day=day,
        stage=engine.event,
        spend=spend(engine.state),
        spend_ceiling_usd=os.environ.get(CEILING),
        bug_errors_today=sum(bugs.values()),
        free_disk_gb=round(shutil.disk_usage(engine.run).free / 1e9, 1),
        **extra,
    )
    path = engine.run / "heartbeat.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


@contextmanager
def stall_guard(run, minutes=STALL_MINUTES):
    """Raise Stalled in the main thread when the run's trace has not grown for `minutes`."""
    trace, limit, started = Path(run) / "trace.jsonl", minutes * 60, time.time()

    def check(*_):
        last = max(started, trace.stat().st_mtime if trace.exists() else 0)
        if time.time() - last > limit:
            raise Stalled(f"No trace activity for {minutes} minutes")

    previous = signal.signal(signal.SIGALRM, check)
    tick = min(60, limit / 4)
    signal.setitimer(signal.ITIMER_REAL, tick, tick)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
