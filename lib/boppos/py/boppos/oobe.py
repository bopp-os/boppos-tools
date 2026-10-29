"""The model of the first-boot setup (`boppos-deck --oobe`) for a system installed with `bootc install`, which
has no user yet. GTK-free so it can be tested. It is a dry run: nothing here creates a user or changes the system.

The real version hands these choices to a narrow root helper (see the plan); the helper must re-validate every
value, because this module only keeps a honest user from typing a bad one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DRY_RUN = True

SESSIONS = {
    "gaming": ("Gaming Mode", "Starts straight into Steam, like a console."),
    "desktop": ("Desktop", "Starts the desktop. Gaming Mode stays one click away."),
}

# Names that already mean something on a Linux system, so an account may not take them.
RESERVED_USERNAMES = frozenset({
    "root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news", "uucp", "proxy", "nobody",
    "wheel", "sudo", "admin", "administrator", "guest", "user", "boppos-oobe", "gdm", "sddm", "polkitd",
    "systemd-network", "systemd-resolve", "dbus", "avahi", "flatpak", "steam",
})

_USERNAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_LOCALE = re.compile(r"^[a-z]{2,3}_[A-Z]{2}(\.UTF-8)?$")
_KEYMAP = re.compile(r"^[A-Za-z0-9_.+-]{1,40}$")
_ZONE = re.compile(r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+){0,2}$")


@dataclass(frozen=True)
class Step:
    key: str
    title: str


STEPS = (
    Step("welcome", "Welcome"),
    Step("language", "Language"),
    Step("network", "Network"),
    Step("timezone", "Time zone"),
    Step("account", "Account"),
    Step("session", "Start in"),
    Step("review", "Review"),
    Step("done", "Done"),
)


@dataclass
class Choices:
    locale: str = "en_US.UTF-8"
    keymap: str = "us"
    timezone: str = "UTC"
    hostname: str = ""
    fullname: str = ""
    username: str = ""
    password: str = field(default="", repr=False)  # never shown, logged or put in the plan
    session: str = "desktop"


# --- validation ---------------------------------------------------------------------------------------------


def username_problem(name: str) -> str | None:
    if not name:
        return "Pick a user name."
    if name in RESERVED_USERNAMES:
        return f"{name!r} is used by the system. Pick another name."
    if not _USERNAME.fullmatch(name):
        return "Use lowercase letters, digits, - and _, starting with a letter, up to 32 characters."
    return None


def fullname_problem(name: str) -> str | None:
    if len(name) > 64:
        return "Keep the full name under 64 characters."
    if any(ch in name for ch in ":,=\\\"'") or any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        return "The full name may not contain : , = quotes, backslashes or control characters."
    return None


def hostname_problem(name: str) -> str | None:
    """An empty hostname is fine: the system keeps its default."""
    if not name:
        return None
    if len(name) > 63 or not _LABEL.fullmatch(name):
        return "Use lowercase letters, digits and -, not starting or ending with -, up to 63 characters."
    return None


def password_hint(password: str) -> tuple[str, str]:
    """(level, text): "empty", "weak", "ok" or "strong". Only a hint; a short password is refused, nothing else."""
    if not password:
        return "empty", "Choose a password."
    if len(password) < 8:
        return "weak", "Use at least 8 characters."
    classes = sum(bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if len(password) >= 14 or classes >= 3:
        return "strong", "Good."
    return "ok", "Fine. A longer password or more kinds of characters is stronger."


def locale_problem(value: str, known: list[str] | None = None) -> str | None:
    if not _LOCALE.fullmatch(value) or (known and value not in known):
        return "Pick a language from the list."
    return None


def keymap_problem(value: str, known: list[str] | None = None) -> str | None:
    if not _KEYMAP.fullmatch(value) or (known and value not in known):
        return "Pick a keyboard layout from the list."
    return None


def timezone_problem(value: str, known: list[str] | None = None) -> str | None:
    if not _ZONE.fullmatch(value) or ".." in value or (known and value not in known):
        return "Pick a time zone from the list."
    return None


def step_problem(step: str, c: Choices) -> str | None:
    """Why the user may not leave this step yet, or None."""
    if step == "account":
        if problem := username_problem(c.username) or fullname_problem(c.fullname) or hostname_problem(c.hostname):
            return problem
        if password_hint(c.password)[0] in ("empty", "weak"):
            return password_hint(c.password)[1]
    if step == "language":
        return locale_problem(c.locale) or keymap_problem(c.keymap)
    if step == "timezone":
        return timezone_problem(c.timezone)
    if step == "session" and c.session not in SESSIONS:
        return "Pick where to start."
    return None


def problems(c: Choices) -> list[str]:
    return [p for s in STEPS if (p := step_problem(s.key, c))]


# --- lists ---------------------------------------------------------------------------------------------------


COMMON_LOCALES = ["en_US.UTF-8", "en_GB.UTF-8", "de_DE.UTF-8", "fr_FR.UTF-8", "es_ES.UTF-8", "it_IT.UTF-8",
                  "pt_BR.UTF-8", "nl_NL.UTF-8", "pl_PL.UTF-8", "ru_RU.UTF-8", "ja_JP.UTF-8", "ko_KR.UTF-8",
                  "zh_CN.UTF-8", "sv_SE.UTF-8", "tr_TR.UTF-8"]
COMMON_KEYMAPS = ["us", "uk", "de", "fr", "es", "it", "br-abnt2", "nl", "pl", "ru", "jp106", "se", "tr"]


def timezones(root: str | Path = "/") -> list[str]:
    """Zone names from tzdata's zone1970.tab; UTC alone when it is missing."""
    try:
        text = (Path(root) / "usr/share/zoneinfo/zone1970.tab").read_text(errors="replace")
    except OSError:
        return ["UTC"]
    zones = {cols[2] for ln in text.splitlines() if ln and not ln.startswith("#")
             if len(cols := ln.split("\t")) >= 3}
    return ["UTC", *sorted(z for z in zones if not timezone_problem(z))]


def online(root: str | Path = "/") -> bool:
    """Whether any non-loopback interface is up (a wired or Wi-Fi link; this does not prove internet access)."""
    base = Path(root) / "sys/class/net"
    try:
        for dev in base.iterdir():
            if dev.name != "lo" and (dev / "operstate").read_text().strip() == "up":
                return True
    except OSError:
        pass
    return False


# --- plan ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    area: str
    text: str


def build_plan(c: Choices) -> list[Action]:
    """What the root helper would be asked to do, in order. The password is never part of the text."""
    plan = [
        Action("Language", f"Set the language to {c.locale} and the keyboard layout to {c.keymap}"),
        Action("Time", f"Set the time zone to {c.timezone}"),
    ]
    if c.hostname:
        plan.append(Action("Network", f"Name this computer {c.hostname}"))
    who = f"{c.username} ({c.fullname})" if c.fullname else c.username
    plan.append(Action("Account", f"Create the user {who} as an administrator, with the password you chose"))
    name = SESSIONS.get(c.session, (c.session,))[0]
    plan.append(Action("Start", f"Sign {c.username} in automatically to {name}"))
    plan.append(Action("Finish", "Mark setup as done and start the login screen"))
    return plan


def run_plan(plan: list[Action]) -> list[str]:
    """Dry run: report what would happen. Deliberately has no way to change the system."""
    return [f"[dry run] would: {a.area}: {a.text}" for a in plan]


@dataclass(frozen=True)
class HelperCall:
    """One `oobe-helper` invocation. stdin carries the password; it is deliberately not part of repr."""
    args: tuple[str, ...]
    stdin: str = field(default="", repr=False)


def helper_calls(c: Choices) -> list[HelperCall]:
    """The ordered oobe-helper calls that apply the choices. The account is created before it is signed in."""
    calls = [HelperCall(("set-locale", c.locale)), HelperCall(("set-keymap", c.keymap)),
             HelperCall(("set-timezone", c.timezone))]
    if c.hostname:
        calls.append(HelperCall(("set-hostname", c.hostname)))
    create = ("create-user", c.username, *([c.fullname] if c.fullname else []))
    calls += [HelperCall(create, c.password + "\n"), HelperCall(("set-autologin", c.username, c.session)),
              HelperCall(("finish",))]
    return calls


def apply_choices(c: Choices, helper_cmd, run) -> tuple[bool, str]:
    """Run the helper calls in order and stop at the first failure.

    helper_cmd(verb_and_args) -> argv (pkexec plus the helper path); run(argv, stdin) -> (returncode, output).
    Refuses to do anything while a choice is invalid, and never reports the password."""
    if problems(c):
        return False, "Setup is not complete: " + "; ".join(problems(c))
    for call in helper_calls(c):
        code, out = run(helper_cmd(list(call.args)), call.stdin)
        if code != 0:
            return False, f"{call.args[0]} failed: {out.strip() or f'exit {code}'}"
    return True, "Setup finished."
