"""Locations shared by the Norvale launchers and release scripts."""

from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
SOURCE_LAYOUT = Path("packages/environments/src/environments/norvale")
CHECKOUT = next((parent for parent in PACKAGE.parents if parent / SOURCE_LAYOUT == PACKAGE), Path.cwd())
# The collector's archive inside a run directory; the only part an extractor may read, with observable/days.
RUN_ARCHIVE = "observable/lake"
