"""Compatibility contracts at the artifact entry points."""

from importlib.metadata import version
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from time import monotonic
from unittest import TestCase
from unittest.mock import patch

from components.images import PINS
from engine import Engine, _journal
from engine.versioning import stamp
from environments.datasets import load_release_manifest
from environments.norvale.native.incremental import execute
from environments.norvale.simulate import load_scenario, validate_images


class Desk:
    config = {}


class VersionLock(TestCase):
    def test_workspace_versions_match(self):
        actual = [version(name) for name in ("enterpriseengine", "enterpriseengine-components",
                                              "enterpriseengine-environments")]
        self.assertEqual(actual, [stamp()["engine_version"]] * 3)

    def test_checkpoint_open_and_read_only_view_reject_legacy_metadata(self):
        for metadata in ({}, {**stamp(), "engine_version": "0.2.9"}, {"engine_version": "0.3.0"},
                         {**stamp(), "schema_version": True}, {**stamp(), "engine_version": "0.4.0"}):
            with self.subTest(metadata=metadata), TemporaryDirectory() as directory:
                path = Path(directory) / "engine.sqlite3"

                def open_engine():
                    return Engine(directory, Desk(), seed=1, native=None, providers=None,
                                  monotonic=monotonic)

                open_engine()
                with sqlite3.connect(path) as db:
                    state = json.loads(db.execute("SELECT data FROM checkpoint").fetchone()[0])
                    for key in stamp():
                        del state[key]
                    db.execute("UPDATE checkpoint SET data = ?", (json.dumps({**state, **metadata}),))
                before = path.read_bytes()
                with self.assertRaisesRegex(ValueError, "Engine checkpoint requires"):
                    open_engine()
                with self.assertRaisesRegex(ValueError, "Engine checkpoint requires"):
                    _journal.read(path)
                self.assertEqual(path.read_bytes(), before)

    def test_dataset_scenario_and_incremental_manifest_reject_unstamped_input(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name, artifact, reader in (
                ("manifest.json", "Dataset release", lambda path: load_release_manifest(path.parent)),
                ("scenario.json", "Norvale scenario", load_scenario),
                ("incremental.json", "Incremental run manifest",
                 lambda path: execute(path.parent, None, 0)),
            ):
                (root / "engine.sqlite3").touch()
                path = root / name
                for metadata in ({}, {**stamp(), "engine_version": "0.2.0"}):
                    path.write_text(json.dumps(metadata))
                    with self.assertRaisesRegex(ValueError, f"{artifact} requires engine_version"):
                        reader(path)
            (root / "manifest.json").write_text(json.dumps(stamp()))
            self.assertEqual(load_release_manifest(root), stamp())

    def test_manifests_and_environment_overrides_must_use_pinned_images(self):
        with patch.dict("os.environ", {}, clear=True):
            validate_images(PINS)
            with self.assertRaisesRegex(ValueError, "gitea image mismatch"):
                validate_images({**PINS, "gitea": "gitea/gitea:latest"})
            with patch.dict("os.environ", {"MES_ODOO_IMAGE": "odoo:18.0"}):
                with self.assertRaisesRegex(ValueError, "odoo image mismatch"):
                    validate_images(PINS)
