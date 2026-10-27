import json
from pathlib import Path

from boppos import kargs_cli

from .test_kargs import BASE_OPTS, CMDLINE, dropin, make_root


def run_cli(root: Path, capsys, *argv: str) -> tuple[int, str]:
    status = root / "bootc.json"
    status.write_text('{"status":{"booted":{"image":{"imageDigest":"sha256:a"}}}}')
    (root / "proc").mkdir(exist_ok=True)
    (root / "proc/cmdline").write_text(CMDLINE)
    rc = kargs_cli.main(["--root", str(root), "--bootc-status", str(status), *argv])
    return rc, capsys.readouterr().out


def test_status_json_marks_booted_entry(tmp_path, capsys):
    root = make_root(tmp_path)
    rc, out = run_cli(root, capsys, "--json", "status")
    data = json.loads(out)
    assert rc == 0 and data["editable"]
    assert [e["booted"] for e in data["entries"]] == [True, False]


def test_list_prints_options(tmp_path, capsys):
    rc, out = run_cli(make_root(tmp_path), capsys, "list")
    assert rc == 0 and out.split() == BASE_OPTS.split()


def test_dropins_json(tmp_path, capsys):
    root = make_root(tmp_path)
    dropin(root, "usr/lib/bopp-kargs.d", "deck", "kargs = a\nmatch.sys_vendor = Valve\n")
    dropin(root, "usr/lib/bopp-kargs.d", "asus", "kargs = b\nmatch.sys_vendor = ASUS\n")
    _, out = run_cli(root, capsys, "--json", "dropins")
    by = {d["name"]: d for d in json.loads(out)["dropins"]}
    assert by["deck"]["matches_device"] and not by["asus"]["matches_device"]


def test_mutation_as_non_root_on_real_root_is_refused(capsys, monkeypatch):
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    rc = kargs_cli.main(["--json", "add", "x"])
    assert rc == 1 and json.loads(capsys.readouterr().out)["code"] == "not-root"


def test_invalid_argument_error_code(tmp_path, capsys):
    rc, out = run_cli(make_root(tmp_path), capsys, "--json", "add", "--dry-run", "a b")
    assert rc == 1 and json.loads(out)["code"] == "invalid-arg"


def test_add_dry_run_reports_plan(tmp_path, capsys):
    rc, out = run_cli(make_root(tmp_path), capsys, "--json", "add", "--dry-run", "foo=1")
    assert rc == 0 and json.loads(out)["added"] == ["foo=1"]
