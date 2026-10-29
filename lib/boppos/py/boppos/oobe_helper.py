"""What the root helper `oobe-helper` may do during a first run, as pure functions.

Every verb maps to fixed commands or one fixed file; every argument is validated again here (the UI's checks are
only a courtesy). The helper does nothing unless a first run is pending (/var/lib/boppos/first-run, see
boppos.firstrun). The setup verbs only work while nobody has a real account, so a leftover helper on an installed
system cannot create a second one or change the system settings. The password only ever travels on stdin to
chpasswd, never in an argument list.

Setup (no real account yet; called by boppos-oobe.service's boppos-oobe account):
  set-locale, set-keymap, set-timezone, set-hostname, create-user, then set-start-in and finish-setup.
Welcome tour (a real account exists; called by that user):
  set-start-in USER CHOICE (only for themselves), finish-first-run (removes the marker).
In a chroot, where localectl, timedatectl and hostnamectl cannot reach systemd, the settings are written as files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import firstrun, oobe
from .privileged import UsageError

USERADD = "/usr/sbin/useradd"
CHPASSWD = "/usr/sbin/chpasswd"
LOCALECTL = "/usr/bin/localectl"
TIMEDATECTL = "/usr/bin/timedatectl"
HOSTNAMECTL = "/usr/bin/hostnamectl"
SYSTEMCTL = "/usr/bin/systemctl"

SETUP_ACCOUNT = "boppos-oobe"  # the system user boppos-oobe.service runs the setup as
MAX_PASSWORD = 256


@dataclass(frozen=True)
class Run:
    argv: list[str]
    stdin: str | None = None


@dataclass(frozen=True)
class Write:
    path: str
    text: str
    mode: int = 0o644


@dataclass(frozen=True)
class Symlink:
    path: str
    target: str


@dataclass(frozen=True)
class Remove:
    path: str


Step = Run | Write | Symlink | Remove


def human_users(root: str | Path = "/") -> dict[str, int]:
    """The real accounts (boppos.firstrun.real_accounts)."""
    return firstrun.real_accounts(root)


def needed(root: str | Path = "/") -> bool:
    """Whether this system needs the setup now: a first run is pending and nobody has a real account yet."""
    return firstrun.decide(root).mode == "setup"


def _uid_of(root: str | Path, name: str) -> int | None:
    try:
        text = (Path(root) / "etc/passwd").read_text(errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        cols = line.split(":")
        if len(cols) >= 3 and cols[0] == name and cols[2].isdigit():
            return int(cols[2])
    return None


def _merged(root: str | Path, path: str, key: str, value: str) -> str:
    """The text of a KEY=value file with one key set, keeping its other lines (vconsole.conf's FONT=, say)."""
    try:
        lines = (Path(root) / path.lstrip("/")).read_text(errors="replace").splitlines()
    except OSError:
        lines = []
    kept = [ln for ln in lines if not re.match(rf"\s*{re.escape(key)}\s*=", ln)]
    return "\n".join([*kept, f"{key}={value}"]) + "\n"


def _fail(problem: str | None) -> None:
    if problem:
        raise UsageError(problem)


def _expect(argv: list[str], n: int, usage: str) -> None:
    if len(argv) != n:
        raise UsageError(f"usage: {usage}")


def _password(stdin: str | None) -> str:
    if stdin is None:
        raise UsageError("the password is read from stdin")
    pw = stdin[:-1] if stdin.endswith("\n") else stdin
    if "\n" in pw or "\r" in pw or "\0" in pw or len(pw) > MAX_PASSWORD:
        raise UsageError("the password may not contain line breaks and must be at most 256 characters")
    if oobe.password_hint(pw)[0] in ("empty", "weak"):
        raise UsageError(oobe.password_hint(pw)[1])
    return pw


USAGE = ("usage: oobe-helper create-user NAME [FULLNAME] | set-locale L | set-keymap K | set-timezone Z | "
         "set-hostname H | set-start-in USER gaming|desktop | finish-setup | finish-first-run")
SETUP_VERBS = frozenset({"create-user", "set-locale", "set-keymap", "set-timezone", "set-hostname"})
ALIASES = {"set-autologin": "set-start-in", "finish": "finish-setup"}  # the names before first-run was one flow


def steps_for(argv: list[str], stdin: str | None = None, root: str | Path = "/", *, chroot: bool = False,
              caller: int | None = None) -> list[Step]:
    """The commands and files for one helper call. Raises UsageError for anything not allowed right now.

    caller: the uid that asked (pkexec's PKEXEC_UID); None when root ran the helper itself.
    """
    if not argv:
        raise UsageError(USAGE)
    if firstrun.read_hint(root) is None:
        raise UsageError("no first run is pending; this helper does nothing")
    verb, rest = ALIASES.get(argv[0], argv[0]), argv[1:]
    users = human_users(root)
    if verb in SETUP_VERBS and users:
        raise UsageError("an account already exists, so setup is over; this helper does not change the system now")

    if verb == "create-user":
        if len(rest) not in (1, 2):
            raise UsageError("usage: create-user NAME [FULLNAME]")
        name, fullname = rest[0], rest[1] if len(rest) == 2 else ""
        _fail(oobe.username_problem(name) or oobe.fullname_problem(fullname))
        pw = _password(stdin)
        return [Run([USERADD, "--create-home", "--groups", "wheel", "--shell", "/bin/bash",
                     "--comment", fullname, "--", name]),
                Run([CHPASSWD], f"{name}:{pw}\n")]

    if stdin:
        raise UsageError("this verb takes no stdin")
    if verb == "set-locale":
        _expect(rest, 1, "set-locale LOCALE")
        _fail(oobe.locale_problem(rest[0], oobe.COMMON_LOCALES))
        if chroot:
            return [Write("/etc/locale.conf", _merged(root, "/etc/locale.conf", "LANG", rest[0]))]
        return [Run([LOCALECTL, "set-locale", f"LANG={rest[0]}"])]
    if verb == "set-keymap":
        _expect(rest, 1, "set-keymap KEYMAP")
        _fail(oobe.keymap_problem(rest[0], oobe.COMMON_KEYMAPS))
        if chroot:
            return [Write("/etc/vconsole.conf", _merged(root, "/etc/vconsole.conf", "KEYMAP", rest[0]))]
        return [Run([LOCALECTL, "set-keymap", rest[0]])]
    if verb == "set-timezone":
        _expect(rest, 1, "set-timezone ZONE")
        _fail(oobe.timezone_problem(rest[0], oobe.timezones(root)))
        if chroot:
            return [Symlink("/etc/localtime", f"../usr/share/zoneinfo/{rest[0]}")]
        return [Run([TIMEDATECTL, "set-timezone", rest[0]])]
    if verb == "set-hostname":
        _expect(rest, 1, "set-hostname NAME")
        _fail(oobe.hostname_problem(rest[0]))
        if not rest[0]:
            raise UsageError("give a computer name, or do not call this")
        if chroot:
            return [Write("/etc/hostname", f"{rest[0]}\n")]
        return [Run([HOSTNAMECTL, "set-hostname", rest[0]])]
    if verb == "set-start-in":
        _expect(rest, 2, "set-start-in USER gaming|desktop")
        user, choice = rest
        if user not in users:
            raise UsageError(f"{user!r} is not a real account on this system")
        if caller is not None and caller not in (users[user], _uid_of(root, SETUP_ACCOUNT)):
            raise UsageError("you can only choose where your own account starts")
        cfg, problem = firstrun.load_start_in(root)
        if cfg is None:
            raise UsageError(problem)
        if choice not in cfg.choices:
            raise UsageError(f"choose one of: {', '.join(cfg.choices)}")
        return [Write(firstrun.autologin_file(cfg), firstrun.autologin_text(cfg, user, choice))]
    if verb == "finish-setup":
        _expect(rest, 0, "finish-setup")
        if not users:
            raise UsageError("create the user first: finishing now would leave nobody able to sign in")
        return [Run([SYSTEMCTL, "start", "--no-block", "display-manager.service"])]
    if verb == "finish-first-run":
        _expect(rest, 0, "finish-first-run")
        if not users:
            raise UsageError("the first run is not over before an account exists")
        if caller is not None and caller not in users.values():
            raise UsageError("only an account holder finishes the first run")
        return [Remove(firstrun.MARKER)]
    raise UsageError(USAGE)
