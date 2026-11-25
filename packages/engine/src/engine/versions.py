"""Compatibility contract shared by persisted artifacts (R4)."""

import json
import re
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version


@lru_cache(maxsize=1)
def _installed():
    packages = {"schema": "1.0.0", "engine": version("enterpriseengine")}
    for package in ("components", "environments"):
        try:
            packages[package] = version("enterpriseengine-" + package)
        except PackageNotFoundError:
            packages[package] = None
    return packages


def artifact_versions():
    """Return a new stamp. Null identifies an optional package absent at creation."""
    return _installed().copy()


def require_versions(artifact, kind, *, allow_unstamped=False):
    saved = artifact.get("versions") if isinstance(artifact, dict) else None
    if not isinstance(saved, dict):
        if allow_unstamped:
            return
        raise ValueError(f"{kind} has no version stamp; pre-0.3 artifacts cannot be loaded")
    for name, expected in _installed().items():
        actual = saved.get(name)
        if name in saved:
            if actual is None and expected is None:
                continue
            if (
                isinstance(actual, str)
                and expected
                and re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", actual)
                and actual.split(".")[:2] == expected.split(".")[:2]
            ):
                continue
        raise ValueError(f"Incompatible {kind} {name} version: expected {expected}, found {actual!r}")


def load_artifact(path, kind, *, allow_unstamped=False):
    """Load a saved JSON artifact only after checking its writer's versions."""
    artifact = json.loads(path.read_text(encoding="utf-8"))
    require_versions(artifact, kind, allow_unstamped=allow_unstamped)
    return artifact
