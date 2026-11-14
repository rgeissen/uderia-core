#!/usr/bin/env python3
"""Retire the prompt-history rows no licence opens — an operator's decision, run with the server DOWN.

The conversion that retired prompt encryption (agent/prompt_migration.py) leaves a row it cannot
open as it is and reports it at every start. When the licence that keyed such rows no longer exists
anywhere, this replaces those HISTORY rows (prompt_versions only; never a base prompt, never an
override) with a plain marker naming what happened. The database must not be open in a server.

    python maintenance/retire_prompt_history.py --dry-run     # what would be retired
    python maintenance/retire_prompt_history.py               # retire
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(ROOT / "tda_auth.db"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    os.environ.setdefault("UDERIA_NO_PLUGINS", "1")
    from trusted_data_agent.agent import prompt_migration as PM
    r = PM.retire_unreadable_history(args.db, dry_run=args.dry_run)
    print(r)
    return 0 if r["status"] in ("retired", "nothing", "dry_run") else 1


if __name__ == "__main__":
    sys.exit(main())
