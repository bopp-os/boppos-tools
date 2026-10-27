"""Where shared data files live: /usr/share/boppos when installed, share/boppos in a checkout."""

from __future__ import annotations

import os
from pathlib import Path

# .../lib/boppos/py/boppos/paths.py -> parents[4] is /usr when installed and the repo root in a checkout
_PREFIX = Path(__file__).resolve().parents[4]


def share_file(name: str) -> Path:
    override = os.environ.get("BOPPOS_SHARE_DIR")
    return Path(override or _PREFIX / "share/boppos") / name
