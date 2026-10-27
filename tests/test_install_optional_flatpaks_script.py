import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "bin/install-optional-flatpaks"


def fake_flatpak(tmp_path):
    """A flatpak that lists org.a.A as installed and logs everything else it is asked to do."""
    log = tmp_path / "calls.log"
    exe = tmp_path / "bin" / "flatpak"
    exe.parent.mkdir()
    exe.write_text(
        '#!/bin/sh\n'
        'if [ "$1" = list ]; then\n'
        '  case "$*" in *--user*) echo org.a.A ;; esac\n'
        '  exit 0\n'
        'fi\n'
        f'echo "$@" >> {log}\n'
    )
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe.parent, log


@pytest.mark.skipif(os.geteuid() == 0, reason="as root the script installs system-wide")
def test_interactive_mode_installs_per_user_and_skips_what_is_installed(tmp_path):
    bindir, log = fake_flatpak(tmp_path)
    lst = tmp_path / "list.txt"
    lst.write_text("## Tools\norg.a.A\n# name: B\norg.b.B\n")
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "BOPPOS_FLATPAK_LIST": str(lst),
           "XDG_STATE_HOME": str(tmp_path / "state")}
    res = subprocess.run(["bash", str(SCRIPT)], input="n\n", env=env, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stdout + res.stderr
    calls = log.read_text().splitlines()
    installs = [c for c in calls if c.startswith("install")]
    assert len(installs) == 1 and "--user" in installs[0] and installs[0].endswith("org.b.B")
    assert not any("--system" in c for c in calls)
