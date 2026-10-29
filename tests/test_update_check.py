import json
import subprocess

import pytest
from boppos import update_check as uc


def cp(out="", rc=0, err=""):
    return subprocess.CompletedProcess([], rc, out, err)


def status_json(booted="sha256:a", staged=None, ref="ghcr.io/x/y:latest"):
    def entry(d):
        return {"image": {"image": {"image": ref, "transport": "registry"}, "imageDigest": d}}
    status = {"booted": entry(booted)}
    if staged:
        status["staged"] = entry(staged)
    return json.dumps({"spec": {"image": {"image": ref, "transport": "registry"}}, "status": status})


class Fake:
    """Answers commands by their first words; records every call."""

    def __init__(self, answers, tools=("bootc", "skopeo", "bopp-diff")):
        self.answers, self.calls, self.tools = answers, [], set(tools)

    def run(self, cmd):
        self.calls.append(cmd)
        key = " ".join(cmd[:2])
        res = self.answers.get(key, cp(rc=1))
        return res.pop(0) if isinstance(res, list) else res

    def which(self, name):
        return f"/usr/bin/{name}" if name in self.tools else None


def run_check(answers, tools=("bootc", "skopeo", "bopp-diff")):
    f = Fake(answers, tools)
    return uc.check(f.run, f.which, lambda s: None), f


def test_no_bootc_is_an_error_not_a_crash():
    doc, _ = run_check({}, tools=())
    assert doc["error"] == "bootc not found in PATH" and doc["update_available"] is False
    assert doc["current_image"] == "unknown" and doc["transport"] == "unknown"


def test_bootc_status_failure_and_bad_json_are_reported():
    doc, _ = run_check({"bootc status": cp(rc=1, err="boom")})
    assert doc["error"] == "bootc status failed: boom"
    doc, _ = run_check({"bootc status": cp("not json")})
    assert doc["error"].startswith("Expecting value") and doc["update_available"] is False


def test_an_already_staged_update_is_reported_with_its_diff():
    doc, f = run_check({"bootc status": cp(status_json(staged="sha256:b")), "bopp-diff": cp("+ pkg 1.0")})
    assert doc["update_available"] is True and doc["staged_image"] == "sha256:b"
    assert doc["current_image"] == "sha256:a" and doc["diff"] == "+ pkg 1.0" and doc["error"] is None
    assert not any(c[0] == "skopeo" for c in f.calls)  # no registry round trip needed


def test_a_different_registry_digest_is_confirmed_by_bootc():
    doc, f = run_check({"bootc status": cp(status_json()), "skopeo inspect": cp("sha256:new\n"),
                        "bootc upgrade": cp("Available update: sha256:new"), "bopp-diff": cp("diff")})
    assert doc["update_available"] is True and doc["staged_image"] == "sha256:new"
    assert ["skopeo", "inspect", "--format", "{{.Digest}}", "docker://ghcr.io/x/y:latest"] in f.calls
    assert ["bootc", "upgrade", "--check"] in f.calls


def test_offline_install_digest_mismatch_is_not_an_update():
    # installed from containers-storage: the booted digest is local, bootc knows it is current
    doc, f = run_check({"bootc status": cp(status_json(booted="sha256:local")), "skopeo inspect": cp("sha256:reg\n"),
                        "bootc upgrade": cp("No changes in: docker://x")})
    assert doc["update_available"] is False and doc["staged_image"] is None and doc["diff"] is None
    assert not any(c[0] == "bopp-diff" for c in f.calls)


def test_skopeo_same_digest_skips_the_bootc_check():
    doc, f = run_check({"bootc status": cp(status_json()), "skopeo inspect": cp("sha256:a\n")})
    assert doc["update_available"] is False and doc["error"] is None
    assert not any(c[:2] == ["bootc", "upgrade"] for c in f.calls)


def test_bootc_check_finds_an_update_and_prefers_the_restaged_digest():
    answers = {
        "bootc status": [cp(status_json()), cp(status_json(staged="sha256:real"))],
        "bootc upgrade": cp("Available update: sha256:fromcheck"),
    }
    doc, _ = run_check(answers, tools=("bootc",))  # no skopeo, no bopp-diff
    assert doc["update_available"] is True and doc["staged_image"] == "sha256:real"


def test_retries_when_the_network_is_not_up_yet():
    answers = {"bootc status": cp(status_json()),
               "skopeo inspect": [cp(rc=1), cp(rc=1), cp("sha256:a\n")]}
    doc, f = run_check(answers, tools=("bootc", "skopeo"))
    assert doc["update_available"] is False and not any(c[:2] == ["bootc", "upgrade"] for c in f.calls)
    assert sum(1 for c in f.calls if c[0] == "skopeo") == 3


def test_check_upgrade_output():
    assert uc.check_upgrade_output("Available update: sha256:x") == (True, "sha256:x")
    assert uc.check_upgrade_output("No changes in: docker://x") == (False, "")


def test_status_file_is_written_atomically_and_world_readable(tmp_path):
    target = tmp_path / "run" / "boppos" / "update-status.json"
    uc.write_status(uc._failure("x"), target)
    assert json.loads(target.read_text())["error"] == "x"
    assert oct(target.stat().st_mode & 0o777) == "0o644"
    assert not list(target.parent.glob("*.tmp"))


@pytest.mark.parametrize("key", ["update_available", "checked_at", "current_image", "staged_image",
                                 "transport", "diff", "error"])
def test_schema_keys_the_tray_reads_are_all_present(key):
    assert key in uc._failure("x")
