"""bin/bopp-tpm-refresh against stub systemd-cryptenroll/cryptsetup/lsblk and a fake root (BOPP_TPM_ROOT)."""
import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "bin/bopp-tpm-refresh"
UUID = "11111111-2222-3333-4444-555555555555"
SB_VAR = "sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-e098032b8c4c"
SLOTS_OK = "SLOT TYPE\n   0 password\n   1 tpm2\n"


def _exe(bindir, name, body):
    p = bindir / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def run(tmp_path, cmdline=f"root=/dev/mapper/root rd.luks.name={UUID}=root rd.luks.options=tpm2-device=auto",
        crypttab=None, slots=SLOTS_OK, tpm=True, sb=1, lsblk="", initramfs_modules=None, args=(), enroll_rc=0):
    root, bindir, log = tmp_path / "root", tmp_path / "bin", tmp_path / "calls.log"
    (root / "proc").mkdir(parents=True)
    (root / "etc").mkdir(exist_ok=True)
    bindir.mkdir()
    (root / "proc/cmdline").write_text(cmdline + "\n")
    if crypttab is not None:
        (root / "etc/crypttab").write_text(crypttab)
    if sb is not None:
        (root / SB_VAR).parent.mkdir(parents=True)
        (root / SB_VAR).write_bytes(b"\x06\x00\x00\x00" + bytes([sb]))
    if initramfs_modules is not None:
        img = root / "usr/lib/modules" / os.uname().release / "initramfs.img"
        img.parent.mkdir(parents=True)
        img.touch()
        _exe(bindir, "lsinitrd", "printf '%s\\n' " + " ".join(initramfs_modules) + "\n")
    (tmp_path / "slots").write_text(slots)
    _exe(bindir, "systemd-cryptenroll",
         f'echo "cryptenroll $*" >> {log}\n'
         'case "$*" in\n'
         f'  --tpm2-device=list) {"echo PATH DEVICE; echo /dev/tpmrm0 MSFT0101:00" if tpm else "exit 1"} ;;\n'
         f'  --wipe-slot*) exit {enroll_rc} ;;\n'
         f'  *) cat {tmp_path / "slots"} ;;\n'
         'esac\n')
    _exe(bindir, "cryptsetup", f'case "$1" in luksUUID) echo {UUID} ;; esac\n')
    _exe(bindir, "lsblk", f"printf '{lsblk}'\n")
    _exe(bindir, "systemctl", "exit 1\n")
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "BOPP_TPM_ROOT": str(root)}
    res = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=30)
    calls = log.read_text().splitlines() if log.exists() else []
    return res, res.stdout + res.stderr, calls, root


def enrolled(calls):
    return [c for c in calls if "--wipe-slot=tpm2" in c]


def test_cmdline_device_is_enrolled_against_pcr7_and_crypttab_is_left_alone(tmp_path):
    tab = f"# keep me\ndata UUID={UUID} none luks\n"
    res, out, calls, root = run(tmp_path, crypttab=tab)
    assert res.returncode == 0, out
    assert enrolled(calls) == [f"cryptenroll --wipe-slot=tpm2 --tpm2-device=auto --tpm2-pcrs=7 /dev/disk/by-uuid/{UUID}"]
    assert out.count("Key slots") == 2 and "Secure Boot: enabled" in out
    assert (root / "etc/crypttab").read_text() == tab
    assert not list((root / "etc").glob("crypttab.bak*"))


def test_cmdline_without_tpm2_option_says_how_to_add_it(tmp_path):
    res, out, *_ = run(tmp_path, cmdline=f"rd.luks.uuid=luks-{UUID} quiet")
    assert res.returncode == 0, out
    assert "bopp-kargs add rd.luks.options=tpm2-device=auto" in out


def test_never_wipes_the_tpm_slot_when_it_is_the_only_way_in(tmp_path):
    res, out, calls, _ = run(tmp_path, slots="SLOT TYPE\n   0 tpm2\n")
    assert res.returncode != 0 and not enrolled(calls)
    assert "no password or recovery" in out


def test_no_tpm_stops_before_enrolling(tmp_path):
    res, out, calls, _ = run(tmp_path, tpm=False)
    assert res.returncode != 0 and not enrolled(calls) and "No TPM2" in out


def test_pending_first_boot_enrollment_stops(tmp_path):
    (tmp_path / "root/etc/fisherman").mkdir(parents=True)
    (tmp_path / "root/etc/fisherman/tpm2-enroll.key").touch()
    res, out, calls, _ = run(tmp_path)
    assert res.returncode != 0 and calls == [] and "First-boot" in out


def test_several_luks_devices_need_an_argument(tmp_path):
    res, out, calls, _ = run(tmp_path, cmdline="quiet", lsblk="/dev/sda2 crypto_LUKS\\n/dev/sdb1 crypto_LUKS\\n")
    assert res.returncode != 0 and not enrolled(calls)
    assert "/dev/sda2" in out and "/dev/sdb1" in out
    res, out, calls, _ = run(tmp_path / "again", cmdline="quiet",
                             lsblk="/dev/sda2 crypto_LUKS\\n/dev/sdb1 crypto_LUKS\\n", args=["/dev/sdb1"])
    assert res.returncode == 0, out
    assert enrolled(calls)[0].endswith(" /dev/sdb1")


def test_crypttab_only_matching_entry_is_edited_with_backup_and_comments_kept(tmp_path):
    tab = (f"# comment UUID={UUID}\n"
           f"other UUID=99999999-0000-0000-0000-000000000000 none luks\n"
           f"luks-{UUID} UUID={UUID} none luks,discard\n")
    res, out, calls, root = run(tmp_path, cmdline="quiet", crypttab=tab)
    assert res.returncode != 0 and not enrolled(calls), out  # two crypttab entries: needs a device argument
    res, out, calls, root = run(tmp_path / "again", cmdline="quiet", crypttab=tab, args=[f"UUID={UUID}"])
    assert res.returncode == 0, out
    assert enrolled(calls)[0].endswith(f"/dev/disk/by-uuid/{UUID}")
    lines = (root / "etc/crypttab").read_text().splitlines()
    assert lines[0] == f"# comment UUID={UUID}"
    assert lines[1] == "other UUID=99999999-0000-0000-0000-000000000000 none luks"
    assert lines[2] == f"luks-{UUID} UUID={UUID} none luks,discard,tpm2-device=auto"
    [bak] = (root / "etc").glob("crypttab.bak.*")
    assert bak.read_text() == tab


def test_failed_enrollment_is_reported_and_crypttab_untouched(tmp_path):
    tab = f"luks-{UUID} UUID={UUID} none\n"
    res, out, _, root = run(tmp_path, cmdline="quiet", crypttab=tab, enroll_rc=1)
    assert res.returncode != 0 and "enrollment failed" in out
    assert (root / "etc/crypttab").read_text() == tab


@pytest.mark.parametrize("modules,warned", [(["systemd", "crypt", "tpm2-tss"], False), (["systemd", "crypt"], True)])
def test_warns_when_the_initramfs_lacks_tpm2(tmp_path, modules, warned):
    res, out, *_ = run(tmp_path, initramfs_modules=modules)
    assert res.returncode == 0, out
    assert ("no tpm2-tss" in out) == warned


def test_secure_boot_off_is_reported(tmp_path):
    _, out, *_ = run(tmp_path, sb=0)
    assert "Secure Boot: disabled" in out
