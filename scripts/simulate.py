"""Run a versioned Norvale scenario from this checkout."""
from pathlib import Path

from environments.norvale.simulate import main

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    main(checkout=ROOT)
