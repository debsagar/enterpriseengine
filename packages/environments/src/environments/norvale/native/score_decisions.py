"""Run the labelled decision cases against one model. Paid: 40 calls per model.

    uv run python -m environments.norvale.native.score_decisions --model openai/gpt-4.1-mini --out DIR/NAME.jsonl

Uses the company's own prompt, model call and approval holds. Each answer is appended to --out, so an
interrupted run resumes where it stopped. --dry-run builds every prompt and calls nothing.
NORVALE_SPEND_CEILING_USD, when set, stops the run once the costs recorded in --out reach it.
"""

import argparse
import hashlib
import json
from pathlib import Path

from environments.norvale.native import judgment, long_run

CASES = Path(__file__).with_name("decision_cases.json")


def load(path=CASES):
    return json.loads(Path(path).read_text())["cases"]


def prompt(case):
    policies = case.get("policies", [])
    evidence = {"observations": case["evidence"], "prior_cases": [], "effective_policies": policies}
    return judgment.prompt(case["role"], case["as_of"], evidence, case["question"], policies)


def spent(rows):
    costs = [r.get("usage", {}).get("cost") for r in rows]
    return {"spend": {"usd": sum(c or 0 for c in costs), "unpriced_calls": costs.count(None)}}


def fingerprint(case):
    """Of the exact prompt, so a saved answer is never scored against changed evidence or wording."""
    return hashlib.sha256(json.dumps(prompt(case), sort_keys=True).encode()).hexdigest()[:16]


def ask(case, model, rows=()):
    usage, sent, received = {}, prompt(case), []

    def log(kind, **data):
        if kind == "model_response":
            received.append(True)
            usage.update(data["response"].get("usage") or {})

    row = {"id": case["id"], "model": model, "prompt": fingerprint(case)}
    try:
        answer = judgment.complete(sent, log, model=model, before=lambda: long_run.check_spend(spent(rows)))
        row["answer"] = answer
        row["proposed"] = answer.get("action")
        row["executed"] = judgment.graded(sent, dict(answer))["action"]
    except (ValueError, KeyError, TypeError, AttributeError, RuntimeError) as exc:
        if not received:
            raise  # nothing came back (credentials, provider): stop rather than score it
        row["error"] = str(exc)
    return {**row, "usage": usage}


def score(cases, rows):
    """Counts for what the model proposed and for what code let execute."""
    answered = {r["id"]: r for r in rows}
    scored = [c for c in cases if c["id"] in answered]
    out = {"cases": len(cases), "answered": len(scored), "invalid": sum("error" in r for r in answered.values())}
    for stage in ("proposed", "executed"):
        tally = {"correct": 0, "wrong_approvals": [], "wrong_refusals": []}
        for case in scored:
            action = answered[case["id"]].get(stage)
            tally["correct"] += action in case["acceptable"]
            should = case["label"] in judgment.APPROVALS
            if action in judgment.APPROVALS and not should:
                tally["wrong_approvals"].append(case["id"])
            if action not in judgment.APPROVALS and should:
                tally["wrong_refusals"].append(case["id"])
        out[stage] = tally
    for field in ("prompt_tokens", "completion_tokens", "cost"):
        values = [v for r in answered.values() if (v := r.get("usage", {}).get(field)) is not None]
        if values:
            out[field] = round(sum(values), 6)
            out[field + "_per_call"] = round(sum(values) / len(values), 6)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", help="JSONL answers file; required unless --dry-run")
    parser.add_argument("--cases", default=CASES)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    cases = load(args.cases)
    if args.dry_run:
        sizes = [len(json.dumps(prompt(c))) for c in cases]
        print(f"{len(cases)} prompts, {sum(sizes)} characters, largest {max(sizes)}; no model called")
        return None
    if not args.out:
        parser.error("--out is required unless --dry-run")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in out.read_text().splitlines()] if out.exists() else []
    if any(r["model"] != args.model for r in rows):
        parser.error(f"{out} holds another model's answers")
    current = {c["id"]: fingerprint(c) for c in cases}
    if any(r.get("prompt") != current.get(r["id"]) for r in rows):
        parser.error(f"{out} answers different prompts than these cases; use a new --out")
    done = {r["id"] for r in rows}
    with out.open("a") as stream:
        for case in cases:
            if case["id"] not in done:
                rows.append(ask(case, args.model, rows))
                stream.write(json.dumps(rows[-1]) + "\n")
                stream.flush()
                print(case["id"], case["label"], "->", rows[-1].get("proposed", "invalid"), flush=True)
    result = {"model": args.model, **score(cases, rows)}
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    main()
