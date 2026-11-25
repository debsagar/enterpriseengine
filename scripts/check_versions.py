"""Check stable package release versions and Unreleased notes (R4, R20)."""

import argparse
import re
import subprocess
from collections import Counter
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("engine", "components", "environments")


def release_version(project):
    value = tomllib.loads(project)["project"]["version"]
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value):
        raise ValueError(f"expected a stable MAJOR.MINOR.PATCH version, found {value!r}")
    return tuple(map(int, value.split(".")))


def unreleased(notes):
    section = re.search(r"^## \[Unreleased\][ \t]*\n(.*?)(?=^## |\Z)", notes, re.M | re.S)
    if not section:
        raise ValueError("missing ## [Unreleased] changelog section")
    return Counter(re.findall(r"^- (\S[^\n]*)", section[1], re.M))


def check_change(project, notes, previous_project, previous_notes, source_changed):
    current = release_version(project)
    entries = unreleased(notes)
    if previous_project is None:
        return
    previous = release_version(previous_project)
    if current < previous:
        raise ValueError("package version decreased")
    if source_changed and current == previous:
        before = unreleased(previous_notes) if previous_notes is not None else Counter()
        if not entries - before:
            raise ValueError("source changed without a version bump or a new Unreleased entry")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="PR base revision to compare with the checked-out tree")
    base = parser.parse_args().base
    if base:
        base = git("merge-base", base, "HEAD").strip()
    for name in PACKAGES:
        directory = Path("packages", name)
        project = directory / "pyproject.toml"
        notes = directory / "CHANGELOG.md"
        old_project = old_notes = None
        source_changed = False
        if base:
            previous_files = git("ls-tree", "--name-only", base, "--", str(project), str(notes)).splitlines()
            if str(project) in previous_files:
                old_project = git("show", f"{base}:{project}")
            if str(notes) in previous_files:
                old_notes = git("show", f"{base}:{notes}")
            source_changed = bool(git("diff", "--no-renames", "--name-only", base, "--", str(directory / "src")))
        try:
            check_change(
                (ROOT / project).read_text(), (ROOT / notes).read_text(), old_project, old_notes, source_changed
            )
        except (ValueError, OSError, KeyError) as exc:
            raise SystemExit(f"{name}: {exc}") from exc
    print("PASS: 3 package version and changelog checks")


if __name__ == "__main__":
    main()
