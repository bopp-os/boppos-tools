"""Whether a first run is pending and which flow it needs, from the live system. GTK-free, so it can be tested.

The contract with the BoppOS installer (docs/first-run.md):

- /var/lib/boppos/first-run exists while a first run is pending. The installer writes one line, `mode=welcome`
  (it created an account) or `mode=oobe` (it did not). That mode is only a hint: the live system decides.
- No marker: nothing to do. Marker and no real account: setup (create the account, system settings, where to start).
  Marker and a real account: the welcome tour. The tour removes the marker when it finishes.

Everything takes a `root` so tests use fake trees, never the real /etc or /var.
"""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass, field
from pathlib import Path

MARKER = "/var/lib/boppos/first-run"
MODES = ("welcome", "oobe")

# A real account: uid in [FIRST_UID, LAST_UID) and a shell a person can log in with. nobody (65534) and the system
# accounts below 1000 never count, and neither does an account whose shell is nologin or false (whatever directory
# it is in: Arch has /usr/bin/nologin, Debian /usr/sbin/nologin).
FIRST_UID = 1000
LAST_UID = 60000
NO_LOGIN_SHELLS = frozenset({"nologin", "false"})


def _p(root: str | Path, path: str) -> Path:
    return Path(root) / path.lstrip("/")


def is_real_account(uid: int, shell: str) -> bool:
    return FIRST_UID <= uid < LAST_UID and Path(shell).name not in NO_LOGIN_SHELLS


def real_accounts(root: str | Path = "/") -> dict[str, int]:
    """name -> uid of every real account in /etc/passwd. An empty shell field means /bin/sh, so it counts."""
    out: dict[str, int] = {}
    try:
        text = _p(root, "/etc/passwd").read_text(errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        cols = line.split(":")
        if len(cols) < 7 or not cols[2].isdigit():
            continue
        if is_real_account(int(cols[2]), cols[6].strip()):
            out[cols[0]] = int(cols[2])
    return out


def read_hint(root: str | Path = "/") -> str | None:
    """None when no first run is pending; otherwise the installer's mode, or "" when the marker names none we know."""
    try:
        text = _p(root, MARKER).read_text(errors="replace")
    except FileNotFoundError:
        return None
    except OSError:
        return ""  # it is there but unreadable: still pending
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() == "mode" and value.strip() in MODES:
            return value.strip()
    return ""


@dataclass(frozen=True)
class Decision:
    mode: str  # "none", "setup" or "welcome"
    hint: str | None  # what the marker said (None: no marker)
    accounts: dict[str, int] = field(default_factory=dict)

    @property
    def hint_disagrees(self) -> bool:
        """The installer's hint does not match what the live system shows (the live system wins)."""
        expected = {"setup": "oobe", "welcome": "welcome"}.get(self.mode)
        return bool(self.hint and expected and self.hint != expected)


def decide(root: str | Path = "/") -> Decision:
    hint = read_hint(root)
    if hint is None:
        return Decision("none", None)
    accounts = real_accounts(root)
    return Decision("welcome" if accounts else "setup", hint, accounts)


def startup_mode(force: str | None, root: str | Path = "/") -> str:
    """What `boppos-deck` shows at start: "setup", "welcome" or "none" (do nothing).

    force: "first-run" decides from the live system; "setup" (--oobe) and "welcome" (--welcome) force that flow.
    """
    if force in ("setup", "welcome"):
        return force
    if force == "first-run":
        return decide(root).mode
    return "none"


# --- where to start after sign-in (Gaming Mode or Desktop) ------------------------------------------------------

# The image says how its display manager signs a user in and which sessions the two choices mean; boppos-tools
# names no session itself (it would differ per image flavor). See docs/first-run.md.
START_IN_CONF = "/usr/share/boppos/start-in.conf"
CHOICES = ("gaming", "desktop")
SUPPORTED_DISPLAY_MANAGERS = ("sddm",)
SDDM_AUTOLOGIN = "/etc/sddm.conf.d/50-boppos-autologin.conf"
SESSION_DIRS = ("/usr/share/wayland-sessions", "/usr/share/xsessions")
_SESSION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")


@dataclass(frozen=True)
class StartIn:
    display_manager: str
    sessions: dict[str, str]  # choice -> session name (the .desktop file name without .desktop)

    @property
    def choices(self) -> tuple[str, ...]:
        return tuple(c for c in CHOICES if c in self.sessions)


def _session_exists(root: str | Path, name: str) -> bool:
    return any(_p(root, f"{d}/{name}.desktop").is_file() for d in SESSION_DIRS)


def load_start_in(root: str | Path = "/") -> tuple[StartIn | None, str]:
    """(config, problem). The config keeps only choices whose session the image really has; problem says why the
    choice cannot be offered at all (no config, an unsupported display manager, no usable session)."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(_p(root, START_IN_CONF).read_text(errors="replace"))
    except FileNotFoundError:
        return None, f"this image does not say how to sign in automatically ({START_IN_CONF} is missing)"
    except (OSError, configparser.Error) as exc:
        return None, f"cannot read {START_IN_CONF}: {exc}"
    sec = parser["start-in"] if parser.has_section("start-in") else {}
    dm = sec.get("display-manager", "").strip()
    if dm not in SUPPORTED_DISPLAY_MANAGERS:
        known = ", ".join(SUPPORTED_DISPLAY_MANAGERS)
        return None, f"display manager {dm or '(none)'!r} is not supported yet (only {known})"
    sessions = {}
    for choice in CHOICES:
        name = sec.get(f"{choice}-session", "").strip()
        if name and _SESSION_NAME.fullmatch(name) and _session_exists(root, name):
            sessions[choice] = name
    if not sessions:
        return None, "none of the sessions named in the image's start-in.conf is installed"
    return StartIn(dm, sessions), ""


def autologin_file(cfg: StartIn) -> str:
    return {"sddm": SDDM_AUTOLOGIN}[cfg.display_manager]


def autologin_text(cfg: StartIn, user: str, choice: str) -> str:
    return f"[Autologin]\nUser={user}\nSession={cfg.sessions[choice]}\n"


def current_start_in(cfg: StartIn | None, root: str | Path = "/") -> str | None:
    """Which choice the autologin file selects now, so a flow can show it preselected."""
    if cfg is None:
        return None
    try:
        text = _p(root, autologin_file(cfg)).read_text(errors="replace")
    except OSError:
        return None
    match = re.search(r"^Session=(.+)$", text, re.M)
    by_session = {v: k for k, v in cfg.sessions.items()}
    return by_session.get(match.group(1).strip()) if match else None
