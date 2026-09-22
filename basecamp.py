"""Run from a clone or from an installed project's .agents/basecamp/run.py."""
from pathlib import Path
import sys

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here / 'framework' if (here / 'framework').is_dir() else here))
from cli import main

if __name__ == '__main__':
    raise SystemExit(main())
