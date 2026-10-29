"""What the root helper `oobe-helper` may do during the first-boot setup, as pure functions.

Every verb maps to fixed commands or one fixed file; every argument is validated again here (the UI's checks are
only a courtesy). The helper refuses everything once setup is done, and refuses to create a user when a human
account already exists, so a leftover helper on an installed system can do nothing. The password only ever travels
on stdin to chpasswd, never in an argument list.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import oobe
from .privileged import UsageError

USERADD = "/usr/sbin/useradd"
CHPASSWD = "/usr/sbin/chpasswd"
LOCALECTL = "/usr/bin/localectl"
TIMEDATECTL = "/usr/bin/timedatectl"
HOSTNAMECTL = "/usr/bin/hostnamectl"
SYSTEMCTL = "/usr/bin/systemctl"

DONE_FILE = "/var/lib/boppos/oobe-done"
AUTOLOGIN_FILE = "/etc/sddm.conf.d/50-boppos-oobe-autologin.conf"
# SDDM session names for the two choices. UNVERIFIED: confirm against the image's wayland-sessions before shipping.
SESSION_FILES = {"gaming": "gamescope-session-steam", "desktop": "plasma"}

FIRST_HUMAN_UID = 1000
NOBODY_UID = 65534
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


Step = Run | Write


def human_users(root: str | Path = "/") -> dict[str, int]:
    """Accounts in /etc/passwd that belong to a person (uid 1000 and up, not nobody)."""
    out: dict[str, int] = {}
    try:
        text = (Path(root) / "etc/passwd").read_text(errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        cols = line.split(":")
        if len(cols) >= 3 and cols[2].isdigit() and FIRST_HUMAN_UID <= int(cols[2]) < NOBODY_UID:
            out[cols[0]] = int(cols[2])
    return out


def is_done(root: str | Path = "/") -> bool:
    return (Path(root) / DONE_FILE.lstrip("/")).exists()


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
         "set-hostname H | set-autologin USER SESSION | finish")


def steps_for(argv: list[str], stdin: str | None = None, root: str | Path = "/") -> list[Step]:
    """The commands and files for one helper call. Raises UsageError for anything not allowed right now."""
    if not argv:
        raise UsageError(USAGE)
    if is_done(root):
        raise UsageError("setup is already finished; this helper does nothing any more")
    verb, rest = argv[0], argv[1:]
    users = human_users(root)

    if verb == "create-user":
        if len(rest) not in (1, 2):
            raise UsageError("usage: create-user NAME [FULLNAME]")
        name, fullname = rest[0], rest[1] if len(rest) == 2 else ""
        _fail(oobe.username_problem(name) or oobe.fullname_problem(fullname))
        if users:
            raise UsageError("a user account already exists; setup only creates the first one")
        pw = _password(stdin)
        return [Run([USERADD, "--create-home", "--groups", "wheel", "--shell", "/bin/bash",
                     "--comment", fullname, "--", name]),
                Run([CHPASSWD], f"{name}:{pw}\n")]

    if stdin:
        raise UsageError("this verb takes no stdin")
    if verb == "set-locale":
        _expect(rest, 1, "set-locale LOCALE")
        _fail(oobe.locale_problem(rest[0], oobe.COMMON_LOCALES))
        return [Run([LOCALECTL, "set-locale", f"LANG={rest[0]}"])]
    if verb == "set-keymap":
        _expect(rest, 1, "set-keymap KEYMAP")
        _fail(oobe.keymap_problem(rest[0], oobe.COMMON_KEYMAPS))
        return [Run([LOCALECTL, "set-keymap", rest[0]])]
    if verb == "set-timezone":
        _expect(rest, 1, "set-timezone ZONE")
        _fail(oobe.timezone_problem(rest[0], oobe.timezones(root)))
        return [Run([TIMEDATECTL, "set-timezone", rest[0]])]
    if verb == "set-hostname":
        _expect(rest, 1, "set-hostname NAME")
        _fail(oobe.hostname_problem(rest[0]))
        if not rest[0]:
            raise UsageError("give a computer name, or do not call this")
        return [Run([HOSTNAMECTL, "set-hostname", rest[0]])]
    if verb == "set-autologin":
        _expect(rest, 2, "set-autologin USER SESSION")
        user, session = rest
        if user not in users:
            raise UsageError(f"{user!r} is not an account created by this setup")
        if session not in SESSION_FILES:
            raise UsageError(f"session must be one of: {', '.join(SESSION_FILES)}")
        return [Write(AUTOLOGIN_FILE, f"[Autologin]\nUser={user}\nSession={SESSION_FILES[session]}\n")]
    if verb == "finish":
        _expect(rest, 0, "finish")
        if not users:
            raise UsageError("create the user first: finishing now would leave nobody able to sign in")
        return [Write(DONE_FILE, "done\n"), Run([SYSTEMCTL, "start", "--no-block", "display-manager.service"])]
    raise UsageError(USAGE)
