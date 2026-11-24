"""Validate persisted artifact versions before using their contents."""

from importlib.metadata import version
import json
import re

ENGINE_VERSION = version("enterpriseengine")
MINIMUM_ENGINE = (0, 3, 0)
SCHEMA_VERSION = 1


def stamp():
    return {"engine_version": ENGINE_VERSION, "schema_version": SCHEMA_VERSION}


def _version_tuple(value):
    if isinstance(value, str) and re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", value):
        return tuple(map(int, value.split(".")))
    return ()


def require_version(document, artifact):
    """Require an explicit supported engine version and the current schema."""
    engine = document.get("engine_version") if isinstance(document, dict) else None
    actual = _version_tuple(engine)
    if not actual or actual < MINIMUM_ENGINE:
        raise ValueError(f"{artifact} requires engine_version >= 0.3.0 (found {engine!r}); "
                         "create a new artifact with a supported engine")
    if actual > _version_tuple(ENGINE_VERSION):
        raise ValueError(f"{artifact} requires a newer engine ({engine}; installed {ENGINE_VERSION})")
    schema = document.get("schema_version")
    if type(schema) is not int or schema != SCHEMA_VERSION:
        raise ValueError(f"{artifact} requires schema_version {SCHEMA_VERSION} (found {schema!r})")
    return document


def load_versioned(path, artifact):
    return require_version(json.loads(path.read_text()), artifact)
