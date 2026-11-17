"""Run the native v2 generator; use --help for options."""
import runpy
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent/'native'))
if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).resolve().parent/'native/company.py'), run_name='__main__')
