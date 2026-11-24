"""Run a versioned Norvale scenario from this checkout."""
from pathlib import Path

from environments.norvale.simulate import load_scenario, main

ROOT = Path(__file__).resolve().parent

if __name__ == "__main__":
    main(checkout=ROOT)
