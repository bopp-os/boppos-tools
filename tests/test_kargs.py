import os
from pathlib import Path

import pytest
from boppos import kargs
from boppos.kargs import KargError

BASE_OPTS = "quiet rd.quiet splash root=UUID=1234 ostree=/ostree/boot.1/default/abc123/0 rw"
CMDLINE = "BOOT_IMAGE=/vmlinuz " + BASE_OPTS
STATUS = {"staged": None, "rollback_queued": False}


class Recorder:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(argv)


def make_root(tmp: Path, options: str = BASE_OPTS, dmi: dict | None = None) -> Path:
    entries = tmp / "boot/loader/entries"
    entries.mkdir(parents=True)
    (entries / "ostree-1.conf").write_text(f"title BoppOS\nversion 1\noptions {options}\n")
    # An older entry for the rollback deployment, to make sure we never pick it.
    (entries / "ostree-2.conf").write_text(
        "title Old\noptions quiet root=UUID=1234 ostree=/ostree/boot.1/default/old999/0\n"
    )
    for csum, commit in (("abc123", "c0ffee"), ("old999", "0ldc0de")):
        d = tmp / "ostree/boot.1.1/default" / csum
        d.mkdir(parents=True)
        os.symlink(f"../../../../deploy/default/deploy/{commit}.0", d / "0")
    os.symlink("boot.1.1", tmp / "ostree/boot.1")
    d = tmp / "sys/class/dmi/id"
    d.mkdir(parents=True)
    for k, v in (dmi or {"sys_vendor": "Valve", "product_name": "Jupiter"}).items():
        (d / k).write_text(v + "\n")
    return tmp


def dropin(root: Path, where: str, name: str, text: str) -> Path:
    p = root / where / f"{name}.conf"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


# --- token logic ---------------------------------------------------------------------------


def test_remove_is_token_exact():
    toks = kargs.tokenize("quiet rd.quiet quiet=1 splash")
    assert kargs.remove_tokens(toks, ["quiet"]) == ["rd.quiet", "quiet=1", "splash"]


@pytest.mark.parametrize("arg", ["a.b/c=d/e.f", "nvme_core.default_ps_max_latency_us=0", "x=1,2;3"])
def test_special_characters_round_trip(arg):
    assert kargs.remove_tokens(kargs.add_tokens(["quiet"], [arg]), [arg]) == ["quiet"]


def test_add_skips_existing():
    assert kargs.add_tokens(["quiet", "a=1"], ["a=1", "b"]) == ["quiet", "a=1", "b"]


@pytest.mark.parametrize("bad", ["", "a b", "a;rm -rf /", "$(x)", "a=`b`", "a" * 300, "x=\"y\""])
def test_invalid_args_rejected(bad):
    with pytest.raises(KargError):
        kargs.validate_arg(bad)


def test_protected_keys():
    with pytest.raises(KargError):
        kargs.remove_tokens(["ostree=/x"], ["ostree=/x"])
    with pytest.raises(KargError):
        kargs.add_tokens([], ["root=/dev/sda"])


def test_tokenize_keeps_quotes():
    assert kargs.tokenize('a="b c" d') == ['a="b c"', "d"]


# --- entry selection -----------------------------------------------------------------------


def test_target_is_booted_entry_not_file_order(tmp_path):
    root = make_root(tmp_path)
    entry = kargs.target_entry(kargs.load_entries(root), STATUS, CMDLINE)
    assert entry.path.name == "ostree-1.conf"
    assert entry.commit == "c0ffee"


def test_refuses_when_update_staged(tmp_path):
    root = make_root(tmp_path)
    with pytest.raises(KargError) as exc:
        kargs.target_entry(kargs.load_entries(root), {"staged": {"digest": "x"}}, CMDLINE)
    assert exc.value.code == "staged"


def test_refuses_when_rollback_queued(tmp_path):
    root = make_root(tmp_path)
    with pytest.raises(KargError):
        kargs.target_entry(kargs.load_entries(root), {"rollback_queued": True}, CMDLINE)


# --- apply with explicit args --------------------------------------------------------------


def test_add_uses_edit_in_place_and_is_idempotent(tmp_path):
    root = make_root(tmp_path)
    rec = Recorder()
    res = kargs.apply_changes(root, STATUS, CMDLINE, add=["amd_pstate=active", "quiet"], run=rec)
    assert res["added"] == ["amd_pstate=active"] and res["reboot_required"]
    assert rec.calls == [["ostree", "admin", "kargs", "edit-in-place", "--append-if-missing=amd_pstate=active"]]
    rec.calls.clear()
    res = kargs.apply_changes(root, STATUS, CMDLINE, add=["quiet"], run=rec)
    assert not res["changed"] and rec.calls == []


def test_remove_rewrites_full_list_token_exact(tmp_path):
    root = make_root(tmp_path)
    rec = Recorder()
    kargs.apply_changes(root, STATUS, CMDLINE, remove=["quiet"], run=rec)
    (call,) = rec.calls
    assert call[:4] == ["ostree", "admin", "instutil", "set-kargs"]
    assert "rd.quiet" in call and "quiet" not in call[4:]
    assert "ostree=/ostree/boot.1/default/abc123/0" in call


def test_remove_missing_is_noop(tmp_path):
    root = make_root(tmp_path)
    rec = Recorder()
    assert not kargs.apply_changes(root, STATUS, CMDLINE, remove=["nope"], run=rec)["changed"]
    assert rec.calls == []


def test_dry_run_runs_nothing(tmp_path):
    root = make_root(tmp_path)
    rec = Recorder()
    res = kargs.apply_changes(root, STATUS, CMDLINE, add=["x"], run=rec, dry_run=True)
    assert res["changed"] and rec.calls == [] and not res["reboot_required"]


# --- drop-ins ------------------------------------------------------------------------------


def test_etc_overrides_usr_by_name(tmp_path):
    root = make_root(tmp_path)
    dropin(root, "usr/lib/bopp-kargs.d", "perf", "kargs = a=1\n")
    dropin(root, "etc/bopp-kargs.d", "perf", "kargs = a=2\n")
    d = kargs.load_dropins(root)["perf"]
    assert d.source == "etc" and d.kargs == ["a=2"]


def test_dmi_matching(tmp_path):
    root = make_root(tmp_path)
    text = "kargs = x\nmatch.sys_vendor = valve\nmatch.product_name = Jupiter|Galileo\n"
    p = dropin(root, "usr/lib/bopp-kargs.d", "deck", text)
    d = kargs.parse_dropin("deck", "usr", p)
    assert kargs.matches_device(d, {"sys_vendor": "Valve", "product_name": "Galileo"})
    assert not kargs.matches_device(d, {"sys_vendor": "Valve", "product_name": "Other"})
    assert not kargs.matches_device(d, {"sys_vendor": "Valve"})  # missing field never matches
    assert kargs.matches_device(kargs.Dropin("any", "usr", p), {})  # no match.* = every device


def test_invalid_dropin_is_reported_and_ignored(tmp_path):
    root = make_root(tmp_path)
    dropin(root, "usr/lib/bopp-kargs.d", "bad", "kargs = a;b\n")
    dropin(root, "usr/lib/bopp-kargs.d", "root", "kargs = root=/dev/x\n")
    ds = kargs.load_dropins(root)
    assert ds["bad"].error and ds["root"].error
    assert kargs.wanted_tokens(ds, {}) == []


def test_apply_adds_matching_and_tracks_ownership(tmp_path):
    root = make_root(tmp_path)
    dropin(root, "usr/lib/bopp-kargs.d", "deck", "kargs = amd_pstate=active quiet\nmatch.sys_vendor = Valve\n")
    dropin(root, "usr/lib/bopp-kargs.d", "other", "kargs = foo\nmatch.sys_vendor = ASUS\n")
    rec = Recorder()
    res = kargs.apply_dropins(root, STATUS, CMDLINE, run=rec)
    assert res["added"] == ["amd_pstate=active"]
    # `quiet` was already present, so we do not own it.
    assert kargs.read_managed(root) == ["amd_pstate=active"]


def test_apply_is_idempotent(tmp_path):
    root = make_root(tmp_path, BASE_OPTS + " amd_pstate=active")
    dropin(root, "usr/lib/bopp-kargs.d", "deck", "kargs = amd_pstate=active\n")
    kargs.write_managed(root, ["amd_pstate=active"])
    rec = Recorder()
    res = kargs.apply_dropins(root, STATUS, CMDLINE + " amd_pstate=active", run=rec)
    assert not res["changed"] and rec.calls == []


def test_apply_removes_only_managed_when_disabled(tmp_path):
    opts = BASE_OPTS + " amd_pstate=active mine=1"
    root = make_root(tmp_path, opts)
    dropin(root, "usr/lib/bopp-kargs.d", "deck", "kargs = amd_pstate=active\n")
    kargs.write_managed(root, ["amd_pstate=active"])
    kargs.set_enabled(root, "deck", False)
    rec = Recorder()
    res = kargs.apply_dropins(root, STATUS, CMDLINE + " amd_pstate=active mine=1", run=rec)
    assert res["removed"] == ["amd_pstate=active"]
    (call,) = rec.calls
    assert "mine=1" in call and "amd_pstate=active" not in call
    assert kargs.read_managed(root) == []


def test_enable_disable_overrides(tmp_path):
    root = make_root(tmp_path)
    usr = dropin(root, "usr/lib/bopp-kargs.d", "deck", "# comment\nkargs = a\n")
    kargs.set_enabled(root, "deck", False)
    etc = root / "etc/bopp-kargs.d/deck.conf"
    assert etc.exists() and not kargs.load_dropins(root)["deck"].enabled
    kargs.set_enabled(root, "deck", True)
    assert not etc.exists() and kargs.load_dropins(root)["deck"].enabled
    assert usr.read_text() == "# comment\nkargs = a\n"  # /usr is never touched


def test_enable_default_off_dropin_writes_override(tmp_path):
    root = make_root(tmp_path)
    dropin(root, "usr/lib/bopp-kargs.d", "opt", "kargs = a\nenabled = false\n")
    d = kargs.set_enabled(root, "opt", True)
    assert d.enabled and d.source == "etc"


def test_set_enabled_rejects_bad_names(tmp_path):
    root = make_root(tmp_path)
    for name in ("../x", "A", "nope"):
        with pytest.raises(KargError):
            kargs.set_enabled(root, name, True)


def test_risk_fields_and_validation(tmp_path):
    root = make_root(tmp_path)
    p = dropin(root, "usr/lib/bopp-kargs.d", "x", "kargs = a\ncategory = Perf\nrisk_level = HIGH\nrisk = bad idea\n")
    d = kargs.parse_dropin("x", "usr", p)
    assert (d.category, d.risk_level, d.risk, d.error) == ("Perf", "high", "bad idea", None)
    p = dropin(root, "usr/lib/bopp-kargs.d", "y", "kargs = a\nrisk_level = scary\n")
    assert "risk_level" in kargs.parse_dropin("y", "usr", p).error


def test_shipped_catalog_is_valid_and_documents_every_risk():
    from pathlib import Path

    catalog = sorted((Path(__file__).parent.parent / "kargs.d").glob("*.conf"))
    assert len(catalog) >= 5
    names = set()
    for path in catalog:
        d = kargs.parse_dropin(path.stem, "usr", path)
        assert d.error is None, (path.name, d.error)
        assert d.description and d.risk and d.kargs and d.category != "Other", path.name
        assert d.enabled is False, f"{path.name} must be off by default"
        assert not d.match, f"{path.name}: catalog presets apply to every device"
        names.add(path.stem)
    assert len(names) == len(catalog)


# --- boot-critical safeguards and descriptions ------------------------------------------------


@pytest.mark.parametrize("tok", [
    "rootflags=subvol=root", "rootfstype=btrfs", "rd.luks.uuid=abc", "rd.luks.name=x=root", "rd.lvm.lv=vg/root",
    "rd.md.uuid=1", "luks.uuid=1", "rw",
])
def test_remove_refuses_boot_critical_arguments(tok):
    from boppos.kargs_info import is_critical

    assert is_critical(tok)
    with pytest.raises(KargError) as exc:
        kargs.remove_tokens(["quiet", tok], [tok])
    assert exc.value.code == "critical"
    assert kargs.remove_tokens(["quiet", tok], [tok], force=True) == ["quiet"]  # explicit override only


def test_force_never_removes_ostree_or_root():
    with pytest.raises(KargError):
        kargs.remove_tokens(["ostree=/x"], ["ostree=/x"], force=True)
    with pytest.raises(KargError):
        kargs.remove_tokens(["root=UUID=1"], ["root=UUID=1"], force=True)


def test_tpm_auto_unlock_option_is_removable_but_unlock_settings_are_not():
    from boppos.kargs_info import is_critical

    assert not is_critical("rd.luks.options=tpm2-device=auto")
    assert is_critical("rd.luks.uuid=1234")
    assert not is_critical("quiet") and not is_critical("mitigations=off")


def test_apply_changes_refuses_critical_before_running_anything(tmp_path):
    root = make_root(tmp_path, BASE_OPTS + " rootflags=subvol=root")
    rec = Recorder()
    with pytest.raises(KargError):
        kargs.apply_changes(root, STATUS, CMDLINE + " rootflags=subvol=root", remove=["rootflags=subvol=root"],
                            run=rec)
    assert rec.calls == []
    # an ordinary removal still keeps every critical argument in the rewritten list
    kargs.apply_changes(root, STATUS, CMDLINE, remove=["quiet"], run=rec)
    assert "rootflags=subvol=root" in rec.calls[0]


def test_dropins_cannot_set_critical_arguments(tmp_path):
    root = make_root(tmp_path)
    p = dropin(root, "usr/lib/bopp-kargs.d", "evil", "kargs = rootflags=subvol=x\n")
    assert kargs.parse_dropin("evil", "usr", p).error


def test_descriptions_exact_then_key_then_prefix_and_unknown():
    from boppos.kargs_info import describe, details

    assert "TPM" in describe("rd.luks.options=tpm2-device=auto")["summary"]
    assert describe("rootflags=subvol=whatever")["critical"]  # matched by key
    assert describe("rd.luks.name=abc=root")["critical"]  # matched by prefix
    assert describe("totally.unknown=1") is None
    d = details("mitigations=off", set())
    assert d["image_default"] and d["risk_level"] == "high" and not d["critical"]
    assert details("nvme_core.default_ps_max_latency_us=0", {"nvme_core.default_ps_max_latency_us=0"})["managed"]


def test_every_image_default_from_the_image_repo_is_described():
    from boppos.kargs_info import describe

    image = ["amd_pstate=active", "amdgpu.ppfeaturemask=0xffffffff", "split_lock_detect=off", "nowatchdog",
             "nmi_watchdog=0", "mitigations=off", "sysrq_always_enabled=1", "usbcore.autosuspend=-1", "iommu=pt",
             "preempt=full", "amdgpu.gpu_recovery=1", "transparent_hugepage=madvise",
             "transparent_hugepage.defrag=defer+madvise", "rd.luks.options=tpm2-device=auto"]
    for tok in image:
        e = describe(tok)
        assert e and e["summary"] and e.get("image_default"), tok


def test_dropins_json_reports_already_active(tmp_path, capsys):
    from .test_kargs_cli import run_cli

    root = make_root(tmp_path)
    dropin(root, "usr/lib/bopp-kargs.d", "have", "kargs = quiet\ndescription = x\n")
    dropin(root, "usr/lib/bopp-kargs.d", "new", "kargs = threadirqs\n")
    import json

    _, out = run_cli(root, capsys, "--json", "dropins")
    by = {d["name"]: d for d in json.loads(out)["dropins"]}
    assert by["have"]["already_active"] and not by["new"]["already_active"]


# --- the forbidden list, for the CLI under sudo as well as the helper ------------------------------


@pytest.mark.parametrize("arg", ["init=/bin/sh", "systemd.unit=rescue.target", "selinux=0", "module_blacklist=nvme",
                                 "acpi=off", "rd.break", "rd.driver.pre=nvme"])
def test_add_tokens_refuses_forbidden_arguments_unless_forced(arg):
    with pytest.raises(KargError) as exc:
        kargs.add_tokens(["quiet"], [arg])
    assert exc.value.code == "forbidden" and "--force" in str(exc.value)
    assert arg in kargs.add_tokens(["quiet"], [arg], force=True)


def test_ostree_and_root_cannot_be_added_even_with_force():
    for arg in ("ostree=/x", "root=/dev/sda"):
        with pytest.raises(KargError):
            kargs.add_tokens([], [arg], force=True)


def test_apply_changes_refuses_forbidden_add_before_running_anything(tmp_path):
    root = make_root(tmp_path)
    rec = Recorder()
    with pytest.raises(KargError):
        kargs.apply_changes(root, STATUS, CMDLINE, add=["selinux=0"], run=rec)
    assert rec.calls == []
    kargs.apply_changes(root, STATUS, CMDLINE, add=["selinux=0"], run=rec, force=True)
    assert rec.calls  # the explicit override goes through


@pytest.mark.parametrize("tok", ["init=/bin/sh", "systemd.debug_shell", "selinux=0", "acpi=off", "rd.driver.pre=x"])
def test_dropins_cannot_carry_forbidden_arguments(tmp_path, tok):
    root = make_root(tmp_path)
    p = dropin(root, "usr/lib/bopp-kargs.d", "bad", f"kargs = {tok}\n")
    assert kargs.parse_dropin("bad", "usr", p).error


def test_removing_a_forbidden_argument_that_is_present_is_allowed(tmp_path):
    root = make_root(tmp_path, BASE_OPTS + " selinux=0")
    rec = Recorder()
    kargs.apply_changes(root, STATUS, CMDLINE + " selinux=0", remove=["selinux=0"], run=rec)
    assert "selinux=0" not in rec.calls[0] and "quiet" in rec.calls[0]


def test_every_early_boot_argument_counts_as_critical_except_the_harmless_ones():
    from boppos.kargs_info import forbidden_reason, is_critical

    for tok in ("rd.driver.pre=nvme", "rd.driver.blacklist=amdgpu", "rd.luks.uuid=1", "rd.lvm.lv=a/b", "rd.md=0"):
        assert is_critical(tok), tok
    for tok in ("rd.luks.options=tpm2-device=auto", "rd.plymouth=0", "rd.debug", "quiet", "threadirqs"):
        assert not is_critical(tok), tok
    assert forbidden_reason("quiet") is None and "security" in forbidden_reason("selinux=0")


def test_describe_command_reports_known_unknown_and_forbidden(tmp_path, capsys):
    import json

    from .test_kargs_cli import run_cli

    root = make_root(tmp_path)
    _, out = run_cli(root, capsys, "--json", "describe", "mitigations=off")
    known = json.loads(out)
    assert known["known"] and known["image_default"] and known["forbidden"] is None
    _, out = run_cli(root, capsys, "--json", "describe", "totally.unknown=1")
    assert json.loads(out)["known"] is False
    _, out = run_cli(root, capsys, "--json", "describe", "init=/bin/sh")
    assert "start" in json.loads(out)["forbidden"]


def test_every_argument_a_shipped_preset_adds_is_described_in_the_argument_catalog():
    """Otherwise the app calls our own presets 'unknown' when someone types them by hand."""
    from pathlib import Path

    from boppos.kargs_info import describe

    for path in sorted((Path(__file__).parent.parent / "kargs.d").glob("*.conf")):
        d = kargs.parse_dropin(path.stem, "usr", path)
        for tok in d.kargs:
            entry = describe(tok)
            assert entry and entry["summary"], f"{tok} (from {path.name}) has no entry in kargs-info.json"
            assert entry.get("risk_level") == d.risk_level, f"{tok}: risk level differs from {path.name}"


def test_the_shipped_presets_are_found_on_the_real_root_from_a_checkout():
    """The bug: from a checkout, nothing lived in /usr/lib/bopp-kargs.d, so the app offered no presets."""
    found = kargs.load_dropins("/")
    assert {"10-nvme-no-apst", "20-sleep-deep", "60-usb-mouse-1khz", "80-intel-iommu-on"} <= set(found)
    assert all(d.error is None and d.source == "usr" for d in found.values() if d.path.parent.name == "kargs.d")


def test_catalog_directory_prefers_the_installed_copy_and_honours_the_override(tmp_path, monkeypatch):
    from boppos import paths

    monkeypatch.setenv("BOPPOS_KARGS_DIR", str(tmp_path))
    assert paths.kargs_catalog_dir() == tmp_path
    monkeypatch.delenv("BOPPOS_KARGS_DIR")
    assert paths.kargs_catalog_dir().name in ("kargs.d", "bopp-kargs.d")


def test_a_test_root_does_not_see_the_real_presets(tmp_path):
    assert kargs.load_dropins(tmp_path) == {}


def _preset(tmp_path, text):
    path = tmp_path / "90-x.conf"
    path.write_text(text)
    d = kargs.parse_dropin("90-x", "usr", path)
    assert not d.error
    return d


def test_fit_by_graphics_vendor(tmp_path):
    d = _preset(tmp_path, "kargs = amdgpu.runpm=0\n")
    assert kargs.fit(d, {}, ("amd",)) == ("fits", "")
    assert kargs.fit(d, {}, ("intel", "nvidia"))[0] == "unsupported"
    assert "AMD" in kargs.fit(d, {}, ())[1]
    assert kargs.gpu_of(_preset(tmp_path, "kargs = i915.enable_psr=0\n")) == "intel"
    assert kargs.gpu_of(_preset(tmp_path, "kargs = nvidia-drm.modeset=1\n")) == "nvidia"
    assert kargs.gpu_of(_preset(tmp_path, "kargs = threadirqs\n")) is None


def test_fit_by_model(tmp_path):
    d = _preset(tmp_path, "kargs = foo=1\nmatch.sys_vendor = Valve\n")
    assert kargs.fit(d, {"sys_vendor": "Valve"}, ())[0] == "fits"
    assert kargs.fit(d, {"sys_vendor": "Dell"}, ())[0] == "unsupported"


def test_fit_risky_presets_that_apply_anywhere(tmp_path):
    assert kargs.fit(_preset(tmp_path, "kargs = foo=1\nrisk_level = low\n"), {}, ())[0] == "optional"
    for level in ("medium", "high"):
        got = kargs.fit(_preset(tmp_path, f"kargs = foo=1\nrisk_level = {level}\n"), {}, ())
        assert got[0] == "risky" and level.capitalize() in got[1]


def test_sections_order_and_what_counts_as_on():
    def d(name, fit, enabled=False, matches=True, error="", active=False):
        return {"name": name, "fit": fit, "enabled": enabled, "matches_device": matches, "error": error,
                "already_active": active}

    items = [d("c", "optional"), d("a", "fits"), d("on-risky", "risky", enabled=True), d("u", "unsupported"),
             d("enabled-elsewhere", "unsupported", enabled=True, matches=False), d("bad", "", error="x"),
             d("b", "fits"), d("r", "risky"), d("image-set", "fits", active=True)]
    got = {k: [x["name"] for x in v] for k, v in kargs.sections(items).items()}
    assert list(got) == ["on", "fits", "optional", "risky", "unsupported", "invalid"]
    assert got == {"on": ["image-set", "on-risky"], "fits": ["a", "b"], "optional": ["c"], "risky": ["r"],
                   "unsupported": ["enabled-elsewhere", "u"], "invalid": ["bad"]}


def test_diff_cmdline_shows_only_what_the_user_changed():
    running = "BOOT_IMAGE=/vmlinuz root=UUID=1 ostree=/ostree/boot.1/a rw quiet amdgpu.ppfeaturemask=0xffffffff"
    nxt = ["root=UUID=1", "ostree=/ostree/boot.0/b", "rw", "quiet", "amdgpu.runpm=0", "initrd=/x"]
    assert kargs.diff_cmdline(running, nxt) == {"added": ["amdgpu.runpm=0"],
                                                "removed": ["amdgpu.ppfeaturemask=0xffffffff"]}


def test_diff_cmdline_is_token_exact_and_empty_when_equal():
    assert kargs.diff_cmdline("quiet rd.quiet", ["rd.quiet"]) == {"added": [], "removed": ["quiet"]}
    assert kargs.diff_cmdline("a b=1", ["a", "b=1"]) == {"added": [], "removed": []}
    assert kargs.diff_cmdline("", []) == {"added": [], "removed": []}
