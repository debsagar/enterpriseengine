"""Offline acceptance for the R19/R20 public timeline."""

from html import unescape
import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "contracts/fixtures/timeline.json"
BUILD = ROOT / "scripts/timeline/build.py"


class TimelineTest(unittest.TestCase):
    def build(self, fixture, out):
        return subprocess.run(
            [sys.executable, str(BUILD), "--offline-fixture", str(fixture), "--out", str(out)],
            capture_output=True, text=True,
        )

    def test_fixture_events_in_order_and_public_fields_only(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        with TemporaryDirectory() as temp:
            out = Path(temp) / "site"
            result = self.build(FIXTURE, out)
            self.assertEqual(result.returncode, 0, result.stderr)
            page = (out / "index.html").read_text(encoding="utf-8")
            self.assertEqual((out / "theme.css").read_bytes(), BUILD.with_name("theme.css").read_bytes())

        rows = re.findall(r'<li data-filters="([^"]+)"><article>(.*?)</article></li>', page)
        self.assertEqual(len(rows), len(fixture["expected"]))
        for (filters, row), (action, number, detail) in zip(rows, fixture["expected"]):
            self.assertIn(f'<p class="action">{action}</p>', row)
            self.assertIn(f">#{number} ", row)
            self.assertIn(detail, unescape(row))
            if detail == "needs-human":
                self.assertIn("needs-human", filters.split())
            if action == "PR merged":
                self.assertIn("merge", filters.split())
        stamps = re.findall(r'<time datetime="([^"]+)">', page)
        self.assertEqual(stamps, sorted(stamps, reverse=True))
        self.assertIn("What changed today", page)
        self.assertIn("2 PR updated", page)
        self.assertIn("4 files · +80 / −12", page)
        self.assertIn('href="https://github.com/example/enterprise/pull/50/files"', page)
        self.assertIn("Author model gpt-6.2-sol, effort medium, run author-47", page)
        self.assertIn("model gpt-6.2-astra, effort medium, run review-old", page)
        self.assertIn("#issuecomment-101", page)
        self.assertIn("&lt;script&gt;example&lt;/script&gt;", page)
        self.assertNotIn("PRIVATE-", page)
        self.assertNotIn("unrelated", page)

    def test_private_repository_fails_before_writing_site(self):
        with TemporaryDirectory() as temp:
            temp = Path(temp)
            fixture = temp / "private.json"
            fixture.write_text(json.dumps({
                "now": "2026-11-24T00:00:00Z",
                "responses": {"": {"visibility": "private"}},
            }), encoding="utf-8")
            out = temp / "site"
            result = self.build(fixture, out)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(out.exists())
