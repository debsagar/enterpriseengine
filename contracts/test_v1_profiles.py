"""Offline acceptance tests for the AutoTwin reference profiles."""

from contextlib import redirect_stderr, redirect_stdout
import gzip
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.validation import profile_autotwin
from scripts.validation.profile_autotwin import MANIFEST, profile, source_pin, verify_sources


EXPECTED_SOURCES = {
    "benchmark/p2p_synthetic.xes": (1000, "benchmark"),
    "sales/om/orders.xes": (None, "sales"),
    "forgeflow/quote.xes": (None, "forgeflow"),
    "forgeflow/o2c.xes": (None, "forgeflow"),
    "forgeflow/p2p.xes": (None, "forgeflow"),
    "forgeflow/production.xes": (None, "forgeflow"),
    "bpi2017/bpi2017.xes.gz": (31509, "bpi2017"),
    "bpi2019/BPI_Challenge_2019.xes": (251734, "bpi2019"),
}

# Projected fixture event counts, variant counts and median case spans in seconds.
FIXTURES = {
    "o2c.xes": (32, 1, 2041710),
    "orders.xes": (18, 2, 679250.5),
    "p2p.xes": (12, 2, 489990),
    "p2p_synthetic.xes": (44, 2, 107397.96),
    "production.xes": (14, 1, 1043310),
    "quote.xes": (12, 1, 222660),
}


class TestV1Profiles(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(MANIFEST.read_text())

    def test_locked_manifest_covers_five_collections(self):
        self.assertEqual(self.manifest["schema_version"], 1)
        references = self.manifest["references"]
        self.assertEqual(set(references), set(EXPECTED_SOURCES))
        for relative, (cases, collection) in EXPECTED_SOURCES.items():
            with self.subTest(relative=relative):
                ref = references[relative]
                self.assertEqual(ref["collection"], collection)
                self.assertTrue(ref["upstream"].startswith("https://"))
                self.assertTrue(ref["case_object"])
                self.assertTrue(ref["semantics"])
                self.assertRegex(ref["source"]["sha256"], r"^[0-9a-f]{64}$")
                self.assertGreater(ref["source"]["bytes"], 0)
                result = ref["profile"]
                self.assertGreater(result["cases"], 0)
                if cases is not None:
                    self.assertEqual(result["cases"], cases)
                self.assertGreaterEqual(result["events"], result["cases"])
                self.assertEqual(sum(result["lifecycle"].values()), result["events"])

    def test_attributed_fixture_profiles(self):
        fixtures = [ref["fixture"] for ref in self.manifest["references"].values()
                    if "fixture" in ref]
        self.assertEqual({Path(fixture["path"]).name for fixture in fixtures}, set(FIXTURES))
        for fixture in fixtures:
            path = MANIFEST.parent / fixture["path"]
            with self.subTest(fixture=path.name):
                self.assertEqual(source_pin(path), fixture["source"])
                result = profile(path)
                events, variants, median_span = FIXTURES[path.name]
                self.assertEqual((result["cases"], result["events"], result["variants"]),
                                 (2, events, variants))
                self.assertEqual(result["observed_case_span_seconds"]["median"], median_span)
                if path.name == "p2p_synthetic.xes":
                    self.assertEqual(result["explicit_intervals"], 44)
                    self.assertEqual(result["explicit_interval_seconds"],
                                     {"min": 1, "median": 595, "max": 79200})
                else:
                    self.assertEqual(result["explicit_intervals"], 0)
                    self.assertIsNone(result["explicit_interval_seconds"])

    def test_source_pin_rejects_same_size_change(self):
        fixture = self.manifest["references"]["benchmark/p2p_synthetic.xes"]["fixture"]
        relative = "benchmark/p2p_synthetic.xes"
        lock = {"references": {relative: {"source": fixture["source"]}}}
        with TemporaryDirectory() as directory:
            root = Path(directory)
            copy = root / relative
            copy.parent.mkdir()
            original = (MANIFEST.parent / fixture["path"]).read_bytes()
            copy.write_bytes(original)
            verify_sources(root, lock)
            changed = original.replace(b"example-1", b"example-3", 1)
            self.assertEqual(len(original), len(changed))
            copy.write_bytes(changed)
            with self.assertRaisesRegex(ValueError, "Source pin mismatch: benchmark/"):
                verify_sources(root, lock)

    def test_cli_verifies_local_checkout_and_rejects_stale_profile(self):
        relative = "sales/om/orders.xes"
        fixture = self.manifest["references"][relative]["fixture"]
        fixture_path = MANIFEST.parent / fixture["path"]
        reference = {"source": fixture["source"], "profile": profile(fixture_path)}
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data" / relative
            source.parent.mkdir(parents=True)
            source.write_bytes(fixture_path.read_bytes())
            manifest = root / "manifest.json"
            lock = {"references": {relative: reference}}
            manifest.write_text(json.dumps(lock))
            with patch.object(profile_autotwin, "MANIFEST", manifest):
                with redirect_stdout(StringIO()) as output:
                    profile_autotwin.main(["--autotwin-root", str(root)])
                self.assertEqual(output.getvalue(), f"Verified {relative}\n")

                reference["profile"]["cases"] += 1
                manifest.write_text(json.dumps(lock))
                with redirect_stderr(StringIO()) as error, self.assertRaises(SystemExit) as failure:
                    profile_autotwin.main(["--autotwin-root", str(root)])
                self.assertEqual(failure.exception.code, 1)
                self.assertEqual(error.getvalue(), f"Profile mismatch: {relative}\n")

                original = source.read_bytes()
                changed = original.replace(b"example-1", b"example-3", 1)
                self.assertNotEqual(changed, original)
                self.assertEqual(len(changed), len(original))
                source.write_bytes(changed)
                with redirect_stderr(StringIO()) as error, self.assertRaises(SystemExit) as failure:
                    profile_autotwin.main(["--autotwin-root", str(root)])
                self.assertEqual(failure.exception.code, 1)
                self.assertEqual(error.getvalue(), f"Source pin mismatch: {relative}\n")

    def test_gzip_namespace_lifecycle_and_ties(self):
        log = b"""<log xmlns="http://www.xes-standard.org/">
        <trace><string key="concept:name" value="case-1"/>
          <event><string key="concept:name" value="Later"/>
            <date key="time:timestamp" value="2026-01-01T00:00:05Z"/></event>
          <event><string key="concept:name" value="Z activity"/>
            <string key="lifecycle:transition" value="complete"/>
            <date key="start_timestamp" value="2026-01-01T00:00:00Z"/>
            <date key="time:timestamp" value="2026-01-01T00:00:02.500Z"/></event>
          <event><string key="concept:name" value="A activity"/>
            <string key="lifecycle:transition" value="start"/>
            <date key="time:timestamp" value="2026-01-01T01:00:00+01:00"/></event>
        </trace></log>"""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.xes.gz"
            path.write_bytes(gzip.compress(log))
            result = profile(path)
        self.assertEqual(result["most_frequent_variant"], [
            {"activity": "Z activity", "lifecycle": "complete"},
            {"activity": "A activity", "lifecycle": "start"},
            {"activity": "Later", "lifecycle": None},
        ])
        self.assertEqual(result["equal_time_events_after_first"],
                         {"ordering_time": 1, "event_timestamp": 0})
        self.assertEqual(result["adjacent_source_time_decreases"],
                         {"ordering_time": 1, "event_timestamp": 2})
        self.assertEqual(result["observed_case_span_seconds"]["median"], 5)
        self.assertEqual(result["explicit_interval_seconds"]["median"], 2.5)
        self.assertEqual(result["lifecycle"], {"(absent)": 1, "complete": 1, "start": 1})
