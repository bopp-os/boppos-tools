"""Plain-text support report."""

from __future__ import annotations

import time


def build_report(sections: dict[str, str]) -> str:
    out = [f"BoppOS support report - {time.strftime('%Y-%m-%d %H:%M:%S %z')}", ""]
    for title, body in sections.items():
        out += [f"===== {title} =====", body.rstrip() or "(empty)", ""]
    return "\n".join(out)
