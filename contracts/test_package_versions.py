"""R4/D2 artifact compatibility and R20 per-package PR checks."""

import json
import sqlite3
import unittest
from contextlib import closing, redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from components.registry import Registry
from engine.__main__ import main as engine_cli
from engine.versions import artifact_versions, load_artifact
from environments.norvale.native.incremental import execute

from packages.engine.tests.test_engine_resume_shifts import Consumer, open_engine
from scripts import check_versions
from scripts.check_versions import check_change


class ReleaseContract(unittest.TestCase):
    def test_source_diff_needs_package_specific_release_evidence(self):
        project = '[project]\nversion = "0.3.0"'
        notes = "## [Unreleased]\n\n### Added\n\n- Existing entry\n"
        with self.assertRaisesRegex(ValueError, "source changed without a version bump"):
            check_change(project, notes + "\n## [0.3.0]\n- Historical entry\n", project, notes, True)
        check_change(project, notes + "- New source change\n", project, notes, True)
        check_change(project.replace("0.3.0", "0.3.1"), notes, project, notes, True)

    def test_pr_command_rejects_fixture_diff(self):
        package = Path("packages/components")
        changed = str(package / "src/components/weekly_demand.py")
        project = package / "pyproject.toml"
        notes = package / "CHANGELOG.md"

        def fixture_git(command, *args):
            if command == "merge-base":
                return "fixture-base"
            if command == "ls-tree":
                return f"{project}\n{notes}\n" if str(project) in args else ""
            if command == "show":
                return (check_versions.ROOT / args[0].split(":", 1)[1]).read_text()
            self.assertEqual(command, "diff")
            return changed if args[-1] == str(package / "src") else ""

        with (
            patch.object(check_versions, "git", fixture_git),
            patch("sys.argv", ["check_versions.py", "--base", "fixture-base"]),
            self.assertRaisesRegex(SystemExit, "components: source changed without a version bump"),
        ):
            check_versions.main()

    def test_checkpoint_rejects_mismatch_without_mutation(self):
        with TemporaryDirectory() as tmp:
            run = open_engine(tmp, Consumer())
            database = Path(tmp) / "engine.sqlite3"
            with closing(sqlite3.connect(database)) as db:
                original = json.loads(db.execute("SELECT data FROM checkpoint").fetchone()[0])
                self.assertEqual(original["versions"], artifact_versions())
                for name in ("engine", "components", "environments", "schema", None):
                    with self.subTest(name=name):
                        checkpoint = original.copy()
                        if name is None:
                            del checkpoint["versions"]
                        else:
                            checkpoint["versions"] = {**original["versions"], name: "0.2.0"}
                        encoded = json.dumps(checkpoint)
                        with db:
                            db.execute("UPDATE checkpoint SET data=?", (encoded,))
                        with self.assertRaisesRegex(ValueError, "version"):
                            open_engine(tmp, Consumer())
                        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                            engine_cli(["--run", tmp, "--metrics"])
                        self.assertEqual(db.execute("SELECT data FROM checkpoint").fetchone()[0], encoded)
                original["versions"]["engine"] = "0.3.99"
                with db:
                    db.execute("UPDATE checkpoint SET data=?", (json.dumps(original),))
            self.assertEqual(open_engine(tmp, Consumer()).history(), run.history())

    def test_manifest_refusal_precedes_transport(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "engine.sqlite3").touch()
            (directory / "incremental.json").write_text(
                json.dumps({"versions": {**artifact_versions(), "environments": "9.0.0"}})
            )
            with self.assertRaisesRegex(ValueError, "manifest environments version"):
                execute(directory, None, 0, lambda *_: self.fail("opened transport"))

    def test_results_stay_unstamped_and_company_map_round_trips(self):
        stamp = artifact_versions()
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            dataset = open_engine(directory, Consumer()).export()
            self.assertNotIn("versions", dataset)
            registry = Registry([])
            manifest = {"version": 1, "versions": stamp, "instances": [], "adapters": []}
            company = registry.company_map(manifest)
            for kind, artifact in (("company map", company),):
                self.assertEqual(artifact["versions"], stamp)
                path = directory / "artifact.json"
                path.write_text(json.dumps(artifact))
                self.assertEqual(load_artifact(path, kind), artifact)
                artifact["versions"]["schema"] = "2.0.0"
                path.write_text(json.dumps(artifact))
                with self.assertRaisesRegex(ValueError, "schema version"):
                    load_artifact(path, kind)
            manifest["versions"]["components"] = "1.0.0"
            with self.assertRaisesRegex(ValueError, "manifest.versions"):
                registry.resolve(manifest)
