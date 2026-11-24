"""Offline browser smoke test for the generated component explorer."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
from subprocess import run
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

from components.registry.catalog import builtin_registry
from frontend.build import build


ROOT = Path(__file__).resolve().parents[1]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class ExplorerTest(unittest.TestCase):
    def test_repository_assets_match_the_builtin_registry(self):
        with TemporaryDirectory(dir=ROOT) as directory:
            output = Path(directory)
            build(builtin_registry(), output)
            for name in ("index.html", "explorer.css"):
                self.assertEqual((output / name).read_bytes(), (ROOT / "frontend" / name).read_bytes())

    def test_browser_inspects_all_types_and_follows_injected_relation(self):
        browser = os.environ.get("CHROMIUM_BIN") or next((
            path for name in ("chromium", "chromium-browser", "google-chrome", "chrome")
            if (path := shutil.which(name))
        ), None)
        if browser is None:
            cache = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", Path.home() / ".cache/ms-playwright"))
            browser = next(iter(sorted(
                cache.glob("chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"),
                reverse=True,
            )), None)
        self.assertIsNotNone(browser, "Install Chromium or set CHROMIUM_BIN for the browser smoke test")

        builtin_types = [entry["type"] for entry in builtin_registry().catalog()]
        self.assertTrue(builtin_types)
        target_type = builtin_types[0]
        injected = {
            "version": 1,
            "type": "test.unfamiliar <pixel> / % v1",
            "instance_schema": {"station": "string"},
            "state_schema": {"remaining": "integer"},
            "parameters": {"buffer": "number"},
            "policies": {},
            "emits": {"work.ready": {"count": "integer"}},
            "handles": {"work.delivered": {"reference": "string"}},
            "relations": {"supplier": target_type, "optional": "not.installed"},
            "requires_adapters": [],
            "provides_adapters": [],
            "provenance": "Synthetic browser fixture",
        }

        with TemporaryDirectory(dir=ROOT) as directory:
            scratch = Path(directory)
            output = scratch / "site"
            extras = scratch / "extra.json"
            extras.write_text(json.dumps([injected]), encoding="utf-8")
            run(
                [sys.executable, "-m", "frontend.build", "--output", str(output),
                 "--extra-descriptors", str(extras)], cwd=ROOT, check=True,
            )
            expected = sorted([*builtin_types, injected["type"]])
            fixture = json.dumps([expected, injected["type"], target_type]).replace("<", r"\u003c")
            (output / "smoke.html").write_text(
                """<!doctype html><meta charset="utf-8"><pre id="result">pending</pre>
<script>
const [expected, novel, target] = FIXTURE;
const frame = document.createElement("iframe");
function check(condition, message) {
    if (!condition) throw new Error(message);
}
frame.addEventListener("load", () => {
    try {
        const doc = frame.contentDocument;
        const links = [...doc.querySelectorAll("nav a")];
        check(JSON.stringify(links.map(link => link.textContent)) === JSON.stringify(expected),
              "Catalog navigation did not enumerate every type");
        check(JSON.stringify([...doc.querySelectorAll("article h2")].map(h => h.textContent))
              === JSON.stringify(expected), "Catalog cards did not enumerate every type");
        const novelLink = links.find(link => link.textContent === novel);
        novelLink.click();
        const card = doc.querySelector(novelLink.hash);
        check(card.matches(":target"), "Catalog link did not select the new type");
        const sprite = frame.contentWindow.getComputedStyle(card.querySelector(".sprite"));
        check(sprite.clipPath.startsWith("polygon("), "Generic sprite stylesheet did not load");
        for (const text of ["station string", "remaining integer", "buffer number",
                            "work.ready", "count integer", "work.delivered", "reference string",
                            "not.installed (unregistered)"]) {
            check(card.textContent.includes(text), "Missing descriptor text " + text);
        }
        const relation = card.querySelector(".relations a");
        check(relation.textContent === target, "Relation link label did not match the target type");
        relation.click();
        check(doc.querySelector(":target")?.querySelector("h2").textContent === target,
              "Relation link did not navigate to its target");
        document.querySelector("#result").textContent = "PASS";
    } catch (error) {
        document.querySelector("#result").textContent = error.message;
    }
}, {once: true});
frame.src = "index.html";
document.body.append(frame);
</script>
""".replace("FIXTURE", fixture),
                encoding="utf-8",
            )
            handler = partial(QuietHandler, directory=output)
            with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    result = run(
                        [browser, "--headless", "--disable-gpu", "--disable-background-networking",
                         "--disable-breakpad", "--no-first-run", "--no-sandbox",
                         f"--user-data-dir={scratch / 'browser'}", "--dump-dom",
                         "--virtual-time-budget=5000", f"http://127.0.0.1:{server.server_port}/smoke.html"],
                        env={**os.environ, "TMPDIR": directory,
                             "XDG_CONFIG_HOME": str(scratch / "config"),
                             "XDG_CACHE_HOME": str(scratch / "cache")},
                        capture_output=True, text=True, timeout=30,
                    )
                finally:
                    server.shutdown()
                    thread.join()
            self.assertEqual(result.returncode, 0, "Headless Chromium failed to launch")
            status = result.stdout.partition('<pre id="result">')[2].partition("</pre>")[0]
            self.assertEqual(status, "PASS", "Browser smoke test did not complete successfully")
