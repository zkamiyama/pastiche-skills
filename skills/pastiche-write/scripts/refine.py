#!/usr/bin/env python3
"""Write-skill entry point for the shared, provider-neutral refinement workflow."""
from pathlib import Path
import sys

PROFILE = Path(__file__).resolve().parents[2] / "pastiche-profile" / "scripts"

def main(argv=None):
    if not (PROFILE / "workbench.py").is_file():
        print("error: install sibling pastiche-profile to use audited refinement", file=sys.stderr)
        return 2
    sys.path.insert(0, str(PROFILE))
    import workbench
    return workbench.main(["refine", *(sys.argv[1:] if argv is None else argv)])

if __name__ == "__main__":
    raise SystemExit(main())
