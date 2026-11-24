"""Read the manifest of a dataset published for the current engine schema."""

from pathlib import Path

from engine.versioning import load_versioned


def load_release_manifest(directory):
    path = Path(directory) / "manifest.json"
    if not path.is_file():
        raise ValueError("Dataset release requires a manifest.json with engine and schema versions")
    return load_versioned(path, "Dataset release")
