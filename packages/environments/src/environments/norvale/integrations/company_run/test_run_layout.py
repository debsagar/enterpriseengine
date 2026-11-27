"""The simulator lays out a run, separates private records and verifies what it delivers."""

import io
import json
import os
import subprocess
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from environments.norvale import simulate
from environments.norvale.integrations.lake.adapter import Lake, observation
from environments.norvale.integrations.lake.flows import FLOWS
from environments.norvale.paths import PACKAGE, RUN_ARCHIVE

SCENARIO = dict(version=1, days=2, seed=42, opening_orders=[])
CHAIN = sorted({entity for chain in FLOWS.values() for entity in chain})
DAYS = ("2027-01-01", "2027-01-02")


def generate(run, *, entities=CHAIN, dated=True, failed=(), scans=(1439, 2879), manifests=DAYS, read=("source",)):
    """Write what a finished generator leaves behind, through the real archive writer."""
    from environments.norvale.integrations.company_run.lake import write_daily_manifest

    archive, business = run / RUN_ARCHIVE, run / "business"
    business.mkdir(exist_ok=True)
    token = observation.set(("2027-01-01", True) if dated else None)
    try:
        for entity in entities:
            Lake(archive / "delta", {}).ingest("source", entity, [dict(name=entity + "-1")])
    finally:
        observation.reset(token)
    (archive / "state.json").write_text(
        json.dumps({entity: dict(entity=entity, last_success="2027-01-01") for entity in entities})
    )
    for day in manifests:
        write_daily_manifest(archive, day, set(failed), read)
    (run / "analytics.json").write_text(json.dumps(dict(erp=dict(source="source"), calendar=dict(source="calendar"))))
    events = [dict(kind="lake_scan", business_minute=minute, errors=[]) for minute in scans]
    (business / "trace.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    (business / "behaviour.json").write_text("{}\n")


class RunLayout(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.run_dir = Path(temp.name) / "offline-run"
        self.run_dir.mkdir()
        # A commissioned run, as the launcher leaves it before the generator stage.
        (self.run_dir / "scenario.json").write_text(json.dumps(SCENARIO))
        (self.run_dir / "revision").write_text("abc123\n")
        (self.run_dir / "commissioned").touch()
        from environments.norvale.verify_layout import start_run

        start_run(self.run_dir, SCENARIO, git_sha="abc123", mode="offline")

    def simulate(self, *, crash=False, flags=("--offline",), **records):
        seen = {}

        def generator(command, **options):
            seen.update(options["env"])
            generate(self.run_dir, **records)
            if crash:
                raise subprocess.CalledProcessError(1, command)

        argv = ["norvale-simulate", "--run", str(self.run_dir), "--scenario", str(self.run_dir / "scenario.json")]
        error = io.StringIO()
        code = 0
        with (
            patch("sys.argv", [*argv, *flags]),
            patch.object(simulate.subprocess, "run", side_effect=generator),
            patch.dict(os.environ, dict(OPENROUTER_API_KEY="unused", TYPESAFE_API_KEY="unused", NORVALE_OFFLINE="")),
            redirect_stderr(error),
        ):
            try:
                simulate.main()
            except SystemExit as exc:
                code = exc.code
        report = self.run_dir / "verification.json"
        return code, json.loads(report.read_text()) if report.is_file() else None, seen, error.getvalue()

    def test_complete_run_passes_and_keeps_private_records_outside_observable(self):
        self.assertTrue((PACKAGE / "scenarios/norvale-q1.json").is_file())
        code, report, env, error = self.simulate(read=("source", "calendar"))
        self.assertEqual((code, report["ok"]), (0, True), report)
        self.assertEqual(env["NORVALE_OFFLINE"], "1")
        self.assertFalse(set(simulate.PROVIDER_SETTINGS) & set(env))
        files = {str(p.relative_to(self.run_dir)) for p in self.run_dir.rglob("*") if p.is_file()}
        for name in (
            "run.json",
            "verification.json",
            "observable/days/2027-01-01/manifest.json",
            "observable/days/2027-01-02/manifest.json",
            "private/trace/trace.jsonl",
            "private/reports/behaviour.json",
        ):
            self.assertIn(name, files)
        observable = list((self.run_dir / "observable").rglob("*"))
        self.assertFalse([p for p in observable if p.is_symlink()])
        self.assertFalse([p for p in observable if p.name in ("trace.jsonl", "behaviour.json")])
        record = json.loads((self.run_dir / "run.json").read_text())
        self.assertEqual((record["mode"], record["git_sha"], record["scenario"]), ("offline", "abc123", SCENARIO))
        self.assertTrue(record["finished_at"])

    def test_each_defect_fails_the_run(self):
        code, report, _, error = self.simulate(
            entities=[e for e in CHAIN if e != "Payment Entry"],
            dated=False,
            failed=["plant"],
            scans=(1439,),
            manifests=DAYS[:1],
        )
        self.assertEqual(code, 1)
        self.assertIn("verification.json", error)
        self.assertFalse(report["ok"])
        self.assertEqual(report["missing_days"], ["2027-01-02"])
        self.assertEqual(report["failed_sources"], {"2027-01-01": ["plant"]})
        self.assertEqual(report["unreported_sources"], {"2027-01-01": ["calendar"]})
        self.assertEqual(report["end_of_day_scan_problems"], {"2027-01-02": "no end-of-day scan"})
        self.assertEqual(len(report["undated_rows"]), len(CHAIN) - 1)
        self.assertEqual(report["missing_flow_entities"]["order_to_cash"], {"Payment Entry": "never read"})

    def test_interrupted_generator_is_reported(self):
        code, report, _, _ = self.simulate(crash=True)
        self.assertEqual((code, report["ok"], report["run_completed"]), (1, False, False))

    def test_truncated_trace_line_is_reported(self):
        from environments.norvale.verify_layout import finish_run

        generate(self.run_dir, read=("source", "calendar"))
        with (self.run_dir / "business/trace.jsonl").open("a") as trace:
            trace.write('null\n{"ki')
        report = finish_run(self.run_dir, SCENARIO, completed=True)
        self.assertFalse(report["ok"])
        self.assertEqual(report["end_of_day_scan_problems"], {"trace": "damaged line in the run trace"})
        (self.run_dir / RUN_ARCHIVE / "manifests/2027-01-02.json").write_text("{")
        self.assertEqual(finish_run(self.run_dir, SCENARIO, completed=True)["missing_days"], ["2027-01-02"])

    def test_offline_run_cannot_resume_live(self):
        code, report, env, error = self.simulate(flags=())
        self.assertEqual((code, report, env), (1, None, {}))
        self.assertIn("commissioned offline", error)
