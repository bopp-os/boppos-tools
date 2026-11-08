"""The boppos-unl0kr dracut module. dracut and a real initrd boot cannot run here, so module-setup.sh is sourced
with stub dracut helpers, and the password agent runs against a fake ask-password directory with stub unl0kr,
plymouth and systemd-reply-password. The hardware test plan is in docs/unl0kr-unlock.md."""

import configparser
import os
import shutil
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
MOD = ROOT / "boot/dracut/90boppos-unl0kr"
AGENT = MOD / "boppos-unl0kr-agent"
SCRIPTS = [MOD / "module-setup.sh", AGENT]


def unit(name: str) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    cp.read(MOD / name)
    return cp


def executable(path: Path, body: str) -> Path:
    path.write_text("#!/bin/bash\n" + textwrap.dedent(body))
    path.chmod(0o755)
    return path


# --- packaging -----------------------------------------------------------------------------------


def test_make_install_ships_the_module_where_dracut_looks(tmp_path):
    subprocess.run(["make", "-s", f"DESTDIR={tmp_path}", "install-unl0kr-dracut"], cwd=ROOT, check=True)
    dest = tmp_path / "usr/lib/dracut/modules.d/90boppos-unl0kr"
    assert sorted(p.name for p in dest.iterdir()) == sorted(p.name for p in MOD.iterdir())
    for name in ("module-setup.sh", "boppos-unl0kr-agent"):
        assert os.access(dest / name, os.X_OK), name
    for name in ("boppos-unl0kr-agent.path", "boppos-unl0kr-agent.service"):
        assert (dest / name).stat().st_mode & 0o777 == 0o644, name


def test_make_install_and_the_boot_theme_package_include_the_module():
    makefile = (ROOT / "Makefile").read_text()
    install = next(line for line in makefile.splitlines() if line.startswith("install:"))
    assert "install-unl0kr-dracut" in install.split()
    pkgbuild = (ROOT / "packaging/arch/PKGBUILD").read_text()
    theme = pkgbuild[pkgbuild.index("package_boppos-boot-theme()"):]
    assert "install-unl0kr-dracut" in theme
    assert "'unl0kr: " in theme
    # an optional dependency only: desktops have a keyboard
    assert not any("unl0kr" in line for line in pkgbuild.splitlines() if line.strip().startswith("depends="))


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed")
def test_scripts_pass_shellcheck():
    subprocess.run(["shellcheck", "-S", "style", *map(str, SCRIPTS)], check=True)


# --- module-setup.sh -----------------------------------------------------------------------------


def dracut(tmp_path: Path, call: str, *, unl0kr: bool = True, reply: bool = True, unl0kr_bytes: bytes = b"libdrm.so.2",
           etc_conf: str | None = None) -> subprocess.CompletedProcess:
    """Source module-setup.sh with stub dracut helpers that log their arguments to calls.log."""
    sysroot = tmp_path / "sysroot"
    initdir = tmp_path / "initdir"
    (initdir / "etc").mkdir(parents=True, exist_ok=True)
    (sysroot / "usr/bin").mkdir(parents=True, exist_ok=True)
    (sysroot / "usr/lib/systemd").mkdir(parents=True, exist_ok=True)
    (sysroot / "etc").mkdir(parents=True, exist_ok=True)
    (sysroot / "usr/share/X11/xkb/symbols").mkdir(parents=True, exist_ok=True)
    (sysroot / "usr/share/X11/xkb/symbols/us").write_text("")
    if unl0kr:
        (sysroot / "usr/bin/unl0kr").write_bytes(b"\x7fELF" + unl0kr_bytes)
    if reply:
        executable(sysroot / "usr/lib/systemd/systemd-reply-password", "")
    if etc_conf is not None:
        (sysroot / "etc/unl0kr.conf").write_text(etc_conf)
    script = f"""
        log() {{ printf '%s\\n' "$*" >> {tmp_path}/calls.log; }}
        find_binary() {{ [[ -e $dracutsysrootdir/usr/bin/$1 ]] && echo /usr/bin/$1; }}
        require_binaries() {{ for b; do find_binary "$b" > /dev/null || return 1; done; }}
        for f in inst_binary inst_multiple inst_dir inst_simple inst_script inst_rules instmods dwarn; do
            eval "$f() {{ log $f \\"\\$@\\"; }}"
        done
        SYSTEMCTL=systemctl_stub
        systemctl_stub() {{ log systemctl "$@"; }}
        dracutsysrootdir={sysroot} initdir={initdir} moddir={MOD} systemdsystemunitdir=/usr/lib/systemd/system
        . {MOD}/module-setup.sh
        {call}
    """
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_check_returns_255_so_only_add_dracutmodules_pulls_it_in(tmp_path):
    assert dracut(tmp_path, "check").returncode == 255


@pytest.mark.parametrize("missing", ["unl0kr", "reply"])
def test_check_fails_without_unl0kr_or_systemd_reply_password(tmp_path, missing):
    assert dracut(tmp_path, "check", **{missing: False}).returncode == 1


def test_depends_on_the_systemd_crypt_and_drm_modules(tmp_path):
    deps = dracut(tmp_path, "depends").stdout.split()
    for mod in ("systemd", "crypt", "systemd-cryptsetup", "drm", "bash"):
        assert mod in deps, mod


def test_installkernel_adds_the_handheld_touch_and_hid_drivers(tmp_path):
    dracut(tmp_path, "installkernel")
    mods = (tmp_path / "calls.log").read_text().split()
    for mod in ("usbhid", "hid-generic", "hid-multitouch", "i2c-hid-acpi", "i2c-designware-platform"):
        assert mod in mods, mod
    assert "hid-steam" not in mods


def test_install_ships_unl0kr_the_agent_and_enables_its_path_unit(tmp_path):
    r = dracut(tmp_path, "install")
    assert r.returncode == 0, r.stderr
    log = (tmp_path / "calls.log").read_text()
    assert "inst_binary unl0kr" in log
    assert "/usr/lib/systemd/systemd-reply-password" in log
    assert "/usr/share/X11/xkb/symbols/us" in log
    assert f"inst_script {AGENT} /usr/lib/boppos-unl0kr/boppos-unl0kr-agent" in log
    assert "systemctl -q --root" in log and "enable boppos-unl0kr-agent.path" in log
    assert log.rstrip().endswith("mask systemd-ask-password-plymouth.path"), "its prompt lingers after unl0kr answers"
    conf = (tmp_path / "initdir/etc/boppos-unl0kr.conf").read_text()
    assert "backend=drm" in conf and "default=breezy-dark" in conf
    assert "[keyboard]\nautohide=false\n" in conf, "the on-screen keyboard must show with a controller attached"


def test_install_keeps_fbdev_when_unl0kr_has_no_drm_backend(tmp_path):
    dracut(tmp_path, "install", unl0kr_bytes=b"no drm here")
    assert "backend" not in (tmp_path / "initdir/etc/boppos-unl0kr.conf").read_text()
    assert "without the DRM backend" in (tmp_path / "calls.log").read_text()


def test_install_leaves_a_backend_set_in_etc_unl0kr_conf_alone(tmp_path):
    dracut(tmp_path, "install", etc_conf="[general]\n  backend = fbdev\n")
    assert "backend" not in (tmp_path / "initdir/etc/boppos-unl0kr.conf").read_text()
    dracut(tmp_path, "install", etc_conf="[general]\n#backend=fbdev|drm\n")
    assert "backend=drm" in (tmp_path / "initdir/etc/boppos-unl0kr.conf").read_text()


def test_install_leaves_an_autohide_set_in_etc_unl0kr_conf_alone(tmp_path):
    dracut(tmp_path, "install", etc_conf="[keyboard]\nautohide=true\n")
    assert "autohide" not in (tmp_path / "initdir/etc/boppos-unl0kr.conf").read_text()


# --- units ---------------------------------------------------------------------------------------


def test_units_start_on_the_first_prompt_and_stop_before_switch_root():
    path, service = unit("boppos-unl0kr-agent.path"), unit("boppos-unl0kr-agent.service")
    assert path["Path"]["DirectoryNotEmpty"] == "/run/systemd/ask-password"
    assert path["Install"]["WantedBy"] == "sysinit.target"
    assert service["Service"]["ExecStart"] == "/usr/lib/boppos-unl0kr/boppos-unl0kr-agent"
    for u in (path, service):
        assert u["Unit"]["DefaultDependencies"] == "no"
        assert u["Unit"]["ConditionPathExists"] == "/etc/initrd-release"
        assert "initrd-switch-root.target" in u["Unit"]["Conflicts"].split()
        assert "emergency.service" in u["Unit"]["Conflicts"].split()
        # unlike buffybox's own agent, no ConditionPathExists=!/run/plymouth/pid: it works next to Plymouth
        assert "plymouth" not in u["Unit"]["ConditionPathExists"]


# --- the agent -----------------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path):
    ask = tmp_path / "ask"
    ask.mkdir()
    log = tmp_path / "log"
    executable(tmp_path / "unl0kr", f"""
        printf '%s\\n' "unl0kr $*" >> {log}
        case "${{UNL0KR_MODE:-ok}}" in
            ok) printf 'hunter 2' ;;
            fail) exit 1 ;;
            fail-once) [ -e {tmp_path}/failed ] || {{ : > {tmp_path}/failed; exit 1; }}; printf 'hunter 2' ;;
            hang) exec sleep 30 ;;
        esac
    """)
    executable(tmp_path / "reply", f"""printf '%s\\n' "reply $* <$(cat)>" >> {log}""")
    executable(tmp_path / "plymouth", f"""[ "$1" = --ping ] || printf '%s\\n' "plymouth $*" >> {log}""")
    (tmp_path / "plymouth.pid").write_text("1")
    (tmp_path / "dev/dri").mkdir(parents=True)
    (tmp_path / "dev/dri/card0").touch()
    return {
        **os.environ,
        "BOPPOS_UNL0KR_ASK_DIR": str(ask),
        "BOPPOS_UNL0KR_STATE": str(tmp_path / "state"),
        "BOPPOS_UNL0KR_BIN": str(tmp_path / "unl0kr"),
        "BOPPOS_UNL0KR_REPLY": str(tmp_path / "reply"),
        "BOPPOS_UNL0KR_CONF": str(tmp_path / "override.conf"),
        "BOPPOS_UNL0KR_PLYMOUTH": str(tmp_path / "plymouth"),
        "BOPPOS_UNL0KR_PLYMOUTH_PID": str(tmp_path / "plymouth.pid"),
        "BOPPOS_UNL0KR_ONESHOT": "1",
        "BOPPOS_UNL0KR_DEV": str(tmp_path / "dev"),
        "BOPPOS_UNL0KR_START_TRIES": "1",
        "BOPPOS_UNL0KR_RETRY_DELAY": "0",
        "LOG": str(log),
    }


def ask(env, name="ask.abc", pid=None, notafter=0, message="Please enter passphrase for disk root"):
    path = Path(env["BOPPOS_UNL0KR_ASK_DIR"]) / name
    path.write_text(textwrap.dedent(f"""\
        [Ask]
        PID={pid or os.getpid()}
        Socket=/run/systemd/ask-password/sck.{name[4:]}
        AcceptCached=1
        Echo=0
        NotAfter={notafter}
        Message={message}
    """))
    return path


def run(env, **extra):
    return subprocess.run([str(AGENT)], env={**env, **extra}, capture_output=True, text=True, timeout=20)


def calls(env):
    p = Path(env["LOG"])
    return p.read_text().splitlines() if p.exists() else []


def test_a_prompt_gets_the_typed_passphrase_with_plymouth_handed_over_and_back(env):
    ask(env)
    assert run(env).returncode == 0
    assert calls(env) == [
        "plymouth deactivate",
        "unl0kr -n -m Please enter passphrase for disk root",
        "reply 1 /run/systemd/ask-password/sck.abc <hunter 2>",
        "plymouth reactivate",
    ]
    assert not list(Path(env["BOPPOS_UNL0KR_STATE"]).glob("reply*")), "the passphrase was left on disk"


def test_the_override_config_is_passed_when_present_and_plymouth_is_left_alone_when_not_running(env, tmp_path):
    (tmp_path / "override.conf").write_text("[general]\nbackend=drm\n")
    (tmp_path / "plymouth.pid").unlink()
    ask(env)
    run(env)
    assert calls(env)[0] == f"unl0kr -n -m Please enter passphrase for disk root -C {tmp_path}/override.conf"
    assert not [c for c in calls(env) if c.startswith("plymouth")]


def test_a_failed_unl0kr_leaves_the_prompt_to_plymouth(env):
    ask(env)
    run(env, UNL0KR_MODE="fail")
    assert calls(env) == ["plymouth deactivate", "unl0kr -n -m Please enter passphrase for disk root",
                          "plymouth reactivate"]


def _fake_systemctl(env, tmp_path):
    fake = tmp_path / "systemctl"
    fake.write_text(f"""#!/bin/sh\nprintf '%s\\n' "systemctl $*" >> {env['LOG']}\n""")
    fake.chmod(0o755)
    return {"BOPPOS_UNL0KR_SYSTEMCTL": str(fake)}


def test_plymouths_agent_starts_only_when_unl0kr_fails(env, tmp_path):
    ask(env)
    run(env, UNL0KR_MODE="fail", **_fake_systemctl(env, tmp_path))
    assert calls(env)[-1] == "systemctl --no-block start systemd-ask-password-plymouth.service"


def test_plymouths_agent_stays_off_after_a_good_passphrase(env, tmp_path):
    ask(env)
    run(env, **_fake_systemctl(env, tmp_path))
    assert not [c for c in calls(env) if c.startswith("systemctl")]


def test_a_legion_go_gets_the_rotation_config(env, tmp_path):
    dmi = tmp_path / "product_version"
    dmi.write_text("Legion Go 8APU1\n")
    ask(env)
    run(env, BOPPOS_UNL0KR_DMI_VERSION=str(dmi))
    conf = Path(env["BOPPOS_UNL0KR_STATE"]) / "rotation.conf"
    assert conf.read_text() == "[general]\nrotation=90\n"
    assert f"-C {conf}" in calls(env)[1]


def test_other_devices_get_no_rotation(env, tmp_path):
    dmi = tmp_path / "product_version"
    dmi.write_text("ThinkPad\n")
    ask(env)
    run(env, BOPPOS_UNL0KR_DMI_VERSION=str(dmi))
    assert "rotation.conf" not in calls(env)[1]


def test_unl0kr_is_given_up_after_two_failures_in_a_row(env):
    for n in range(3):
        ask(env, name=f"ask.{n}")
    run(env, UNL0KR_MODE="fail")
    assert len([c for c in calls(env) if c.startswith("unl0kr")]) == 2


def test_an_unl0kr_that_fails_at_start_is_tried_again(env):
    ask(env)
    stderr = run(env, UNL0KR_MODE="fail-once", BOPPOS_UNL0KR_START_TRIES="3").stderr
    assert calls(env) == [
        "plymouth deactivate",
        "unl0kr -n -m Please enter passphrase for disk root",
        "unl0kr -n -m Please enter passphrase for disk root",
        "reply 1 /run/systemd/ask-password/sck.abc <hunter 2>",
        "plymouth reactivate",
    ]
    assert "did not start (exit 1), trying again" in stderr


def test_start_retries_stop_at_the_limit(env):
    ask(env)
    run(env, UNL0KR_MODE="fail", BOPPOS_UNL0KR_START_TRIES="3")
    assert len([c for c in calls(env) if c.startswith("unl0kr")]) == 3


def test_unl0kr_waits_for_the_gpu_driver(env, tmp_path):
    card = tmp_path / "dev/dri/card0"
    card.unlink()
    path = ask(env)
    proc = subprocess.Popen([str(AGENT)], env=env)
    time.sleep(0.6)
    assert calls(env) == [], "unl0kr started before there was a display"
    card.touch()
    assert proc.wait(timeout=5) == 0
    assert path.exists() and calls(env)[1].startswith("unl0kr")


def test_unl0kr_still_runs_when_no_display_turns_up(env, tmp_path):
    (tmp_path / "dev/dri/card0").unlink()
    ask(env)
    stderr = run(env, BOPPOS_UNL0KR_DISPLAY_WAIT="1").stderr
    assert "no display device after 1s" in stderr
    assert [c for c in calls(env) if c.startswith("unl0kr")]


def test_each_prompt_is_answered_once(env):
    ask(env)
    run(env)
    run(env)
    assert len([c for c in calls(env) if c.startswith("reply")]) == 1


def test_a_new_prompt_after_a_wrong_passphrase_is_answered_again(env):
    first = ask(env, name="ask.one")
    run(env)
    first.unlink()
    ask(env, name="ask.two")
    run(env)
    assert len([c for c in calls(env) if c.startswith("reply")]) == 2


@pytest.mark.parametrize("kind", ["dead-asker", "expired"])
def test_stale_prompts_are_skipped(env, kind):
    if kind == "dead-asker":
        p = subprocess.Popen(["true"])
        p.wait()
        ask(env, pid=p.pid)
    else:
        ask(env, notafter=1)
    run(env)
    assert calls(env) == []


def test_unl0kr_is_closed_when_the_prompt_is_answered_elsewhere(env):
    path = ask(env)
    proc = subprocess.Popen([str(AGENT)], env={**env, "UNL0KR_MODE": "hang"})
    deadline = time.monotonic() + 5
    while not [c for c in calls(env) if c.startswith("unl0kr")] and time.monotonic() < deadline:
        time.sleep(0.05)
    path.unlink()
    assert proc.wait(timeout=5) == 0
    assert not [c for c in calls(env) if c.startswith("reply")]
    assert calls(env)[-1] == "plymouth reactivate"


# --- the VM test driver --------------------------------------------------------------------------


def test_vm_test_driver_matches_the_agents_log_lines(env, monkeypatch):
    """tools/vm-test/qmp-drive judges a VM by the agent's log lines; keep the two in step."""
    import importlib.machinery
    import importlib.util
    import sys

    monkeypatch.setattr(sys, "dont_write_bytecode", True)

    loader = importlib.machinery.SourceFileLoader("qmp_drive", str(ROOT / "tools/vm-test/qmp-drive"))
    spec = importlib.util.spec_from_loader("qmp_drive", loader)
    qd = importlib.util.module_from_spec(spec)
    loader.exec_module(qd)

    ask(env)
    stderr = run(env).stderr
    assert qd.PROMPT.search(stderr) and qd.ANSWERED.search(stderr), stderr
    assert not qd.BROKEN.search(stderr)
    failed = run({**env, "BOPPOS_UNL0KR_STATE": env["BOPPOS_UNL0KR_STATE"] + "2"}, UNL0KR_MODE="fail").stderr
    assert qd.BROKEN.search(failed), failed
