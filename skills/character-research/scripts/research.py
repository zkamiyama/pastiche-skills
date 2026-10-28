#!/usr/bin/env python3
"""Delegate to the installed, shared pastiche-profile standard-library engine."""
import os
from pathlib import Path
import runpy
import sys

engine = Path(os.environ.get("PASTICHE_PROFILE_SCRIPTS", "")) if os.environ.get("PASTICHE_PROFILE_SCRIPTS") else Path(__file__).resolve().parents[2] / "pastiche-profile" / "scripts"
if not (engine / "workbench.py").is_file():
    raise SystemExit("Install pastiche-profile alongside this Skill, or set PASTICHE_PROFILE_SCRIPTS to its scripts directory.")
sys.path.insert(0, str(engine))
sys.argv = [str(engine / "workbench.py"), 'research', *sys.argv[1:]]
runpy.run_path(str(engine / "workbench.py"), run_name="__main__")
