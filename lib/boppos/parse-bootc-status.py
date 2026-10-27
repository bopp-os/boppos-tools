#!/usr/bin/env python3
"""Parse `bootc status --format=json` (stdin) into the KEY:value lines update-check.sh reads.

The parsing itself lives in the shared boppos package, so this and bopp-system cannot disagree.
"""
import argparse
import sys
from pathlib import Path

for _p in (Path(__file__).resolve().parent / "py", Path("/usr/lib/boppos/py")):
    if (_p / "boppos").is_dir():
        sys.path.insert(0, str(_p))
        break

from boppos.bootc import legacy_lines  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse bootc status JSON")
    parser.add_argument("--staged-only", action="store_true", help="Only output the staged image digest/ref")
    args = parser.parse_args()
    for line in legacy_lines(sys.stdin.read(), args.staged_only):
        print(line)


if __name__ == "__main__":
    main()
