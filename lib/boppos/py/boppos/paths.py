"""Where shared data files live: /usr/share/boppos when installed, share/boppos in a checkout."""

from __future__ import annotations

import os
from pathlib import Path

# .../lib/boppos/py/boppos/paths.py -> parents[4] is /usr when installed and the repo root in a checkout
_PREFIX = Path(__file__).resolve().parents[4]


def share_file(name: str) -> Path:
    override = os.environ.get("BOPPOS_SHARE_DIR")
    return Path(override or _PREFIX / "share/boppos") / name


def kargs_catalog_dir() -> Path:
    """The packaged kernel-argument presets: /usr/lib/bopp-kargs.d when installed, kargs.d in a checkout."""
    override = os.environ.get("BOPPOS_KARGS_DIR")
    if override:
        return Path(override)
    installed = _PREFIX / "lib/bopp-kargs.d"
    return installed if installed.is_dir() else _PREFIX / "kargs.d"
