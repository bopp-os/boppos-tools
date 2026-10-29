"""Joining Wi-Fi from the first-run setup, through NetworkManager's `nmcli`. GTK-free, so it can be tested.

The setup runs as the no-password `boppos-oobe` user in a logind session, before any account exists, so nmcli runs
as that user (NetworkManager's own polkit rules decide; no root helper, no new privileges here). The Wi-Fi password
never goes into an argument list (anybody can read /proc/<pid>/cmdline): it is handed to nmcli in a 0600 file with
`passwd-file`, which is deleted right after.

Only home-style networks are handled: open, WPA/WPA2 personal and WPA3 personal. Company (802.1X) and WEP networks
are shown as unsupported and left for the desktop's network settings.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

NMCLI = "nmcli"
SCAN_FIELDS = "IN-USE,SSID,SIGNAL,SECURITY"
PSK_KEY = "802-11-wireless-security.psk"  # the secret field of WPA/WPA2 and of WPA3 personal (sae) alike
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

Run = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class Wifi:
    ssid: str
    signal: int  # 0-100
    security: str  # nmcli's words: "WPA2", "WPA1 WPA2", "WPA3", "802.1X", "WEP"; "" for an open network
    in_use: bool = False

    @property
    def open(self) -> bool:
        return not self.security

    @property
    def unsupported(self) -> str:
        """Why this tool cannot join it, or "" when it can."""
        if "802.1X" in self.security:
            return "Needs a company or school login. Join it later from the desktop's network settings."
        if "WEP" in self.security and "WPA" not in self.security:
            return "WEP is too old to be safe. Join it later from the desktop's network settings."
        return ""

    @property
    def key_mgmt(self) -> str:
        """wpa-psk for WPA/WPA2 personal, sae when the network offers WPA3 only."""
        words = self.security.split()
        return "sae" if "WPA3" in words and not any(w in words for w in ("WPA1", "WPA2", "WPA")) else "wpa-psk"


# --- scanning ---------------------------------------------------------------------------------------------------


def split_terse(line: str) -> list[str]:
    """Split one `nmcli -t` line on its colons; `\\:` and `\\\\` inside a value are literal."""
    out, cur, i = [], [], 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if ch == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    out.append("".join(cur))
    return out


def scan_argv(rescan: bool = True) -> list[str]:
    return [NMCLI, "-t", "-f", SCAN_FIELDS, "device", "wifi", "list", "--rescan", "yes" if rescan else "no"]


def parse_scan(text: str) -> list[Wifi]:
    """The networks of `nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY device wifi list`: hidden ones (no name) dropped,
    one entry per name (the strongest signal; the one in use wins), the one in use first, then by signal."""
    best: dict[str, Wifi] = {}
    for line in text.splitlines():
        cols = split_terse(line)
        if len(cols) < 4 or not cols[1].strip():
            continue
        security = "" if cols[3].strip() in ("", "--") else cols[3].strip()
        signal = int(cols[2]) if cols[2].strip().isdigit() else 0
        net = Wifi(cols[1], min(signal, 100), security, cols[0].strip() == "*")
        old = best.get(net.ssid)
        if old is None or (net.in_use, net.signal) > (old.in_use, old.signal):
            best[net.ssid] = net
    return sorted(best.values(), key=lambda n: (not n.in_use, -n.signal, n.ssid.lower()))


def has_wifi_device(device_list: str) -> bool:
    """From `nmcli -t -f TYPE,STATE device`: a Wi-Fi adapter that NetworkManager manages."""
    for line in device_list.splitlines():
        kind, _, state = line.partition(":")
        if kind == "wifi" and state.strip() and state.strip() not in ("unmanaged", "unavailable"):
            return True
    return False


def device_argv() -> list[str]:
    return [NMCLI, "-t", "-f", "TYPE,STATE", "device"]


# --- joining ----------------------------------------------------------------------------------------------------


def ssid_problem(ssid: str) -> str | None:
    if not ssid or len(ssid.encode(errors="replace")) > 32 or _CONTROL.search(ssid):
        return "That is not a usable network name."
    return None


def password_problem(password: str) -> str | None:
    """WPA personal: 8 to 63 characters, or exactly 64 hex digits. No line breaks, they would add lines to the file."""
    if _CONTROL.search(password):
        return "The password may not contain line breaks or control characters."
    if len(password) == 64 and re.fullmatch(r"[0-9A-Fa-f]{64}", password):
        return None
    if not 8 <= len(password) <= 63:
        return "A Wi-Fi password has 8 to 63 characters."
    return None


def join_argvs(net: Wifi, secret_file: str | None) -> list[list[str]]:
    """The nmcli calls that save a connection and bring it up. Secured networks create the profile first (without
    the secret) and activate it with the secret read from `secret_file`."""
    if net.open:
        return [[NMCLI, "device", "wifi", "connect", net.ssid]]
    if secret_file is None:
        raise ValueError("a secured network needs a secret file")
    return [
        [NMCLI, "connection", "add", "type", "wifi", "con-name", net.ssid, "ssid", net.ssid,
         "wifi-sec.key-mgmt", net.key_mgmt],
        [NMCLI, "connection", "up", "id", net.ssid, "passwd-file", secret_file],
    ]


def delete_argv(net: Wifi) -> list[str]:
    return [NMCLI, "connection", "delete", "id", net.ssid]


def friendly(output: str) -> str:
    """One line a person can act on, from nmcli's error text."""
    text = output.lower()
    if "secrets were required" in text or ("password" in text and "wrong" in text):
        return "That password was not accepted."
    if "no network with ssid" in text or "not found" in text:
        return "That network is out of range now. Scan again."
    if "timeout" in text or "timed out" in text:
        return "The network did not answer in time."
    if "not authorized" in text or "insufficient privileges" in text:
        return "This account may not change the network settings."
    last = [ln.strip() for ln in output.splitlines() if ln.strip()]
    return last[-1] if last else "Could not join the network."


def join(net: Wifi, password: str, run: Run = subprocess.run, tmp_dir: str | Path | None = None) -> tuple[bool, str]:
    """Join `net`; (ok, message). A failed secured join removes the profile it just created. The password is never
    in an argument list or in the message."""
    problem = net.unsupported or ssid_problem(net.ssid) or (None if net.open else password_problem(password))
    if problem:
        return False, problem
    secret = None
    try:
        if not net.open:
            base = tmp_dir or os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
            fd, secret = tempfile.mkstemp(dir=base, prefix=".wifi-")
            with os.fdopen(fd, "w") as fh:  # mkstemp already made it 0600
                fh.write(f"{PSK_KEY}:{password}\n")
        for argv in join_argvs(net, secret):
            proc = run(argv, capture_output=True, text=True, timeout=60, check=False)
            if proc.returncode:
                if not net.open:
                    run(delete_argv(net), capture_output=True, text=True, timeout=30, check=False)
                return False, friendly((proc.stderr or "") + (proc.stdout or ""))
        return True, f"Connected to {net.ssid}."
    except subprocess.TimeoutExpired:
        return False, "The network did not answer in time."
    except OSError as exc:
        return False, f"Could not run nmcli: {exc}"
    finally:
        if secret:
            Path(secret).unlink(missing_ok=True)
