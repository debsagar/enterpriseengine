"""Read the recurring controller's state from the engine checkpoint.

The controller keeps its records in the engine's one SQLite checkpoint under
COMPONENT. A pre-engine run's state.json is read, never written.
"""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from engine.versions import load_artifact

COMPONENT = "norvale_company"
ENGINE_DIR = "scheduled-additions"


def load(run, default=None):
    run = Path(run)
    database = run / ENGINE_DIR / "engine.sqlite3"
    if database.exists():
        with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as db:
            row = db.execute("SELECT data FROM checkpoint WHERE id = 1").fetchone()
        saved = json.loads(row[0]).get("state", {}).get(COMPONENT) if row else None
        if saved is not None:
            return saved
    legacy = run / "state.json"
    if legacy.exists():
        return load_artifact(legacy, "company checkpoint", allow_unstamped=True)
    return default
