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
