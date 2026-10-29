import subprocess

import pytest
from boppos import network as net

SCAN = (
    " :HomeNet:55:WPA2\n"
    "*:HomeNet:72:WPA2\n"          # same name, in use: wins whatever the signal
    " :Cafe\\: Free:90:\n"          # an escaped colon in the name, open
    " :Hidden net::WPA2\n"          # no signal figure
    " ::40:WPA2\n"                  # hidden: no name
    " :Office:60:WPA2 802.1X\n"
    " :OldRouter:30:WEP\n"
    " :Modern:80:WPA3\n"
    " :Mixed:20:WPA2 WPA3\n"
    "garbage\n"
)


def by_name(text=SCAN):
    return {n.ssid: n for n in net.parse_scan(text)}


def test_scan_is_sorted_deduped_and_drops_hidden_networks():
    nets = net.parse_scan(SCAN)
    names = [n.ssid for n in nets]
    assert names[0] == "HomeNet" and nets[0].in_use and nets[0].signal == 72
    assert names[1:3] == ["Cafe: Free", "Modern"]  # strongest first
    assert "" not in names and names.count("HomeNet") == 1


def test_open_and_secured_and_unsupported_networks():
    n = by_name()
    assert n["Cafe: Free"].open and n["Cafe: Free"].unsupported == ""
    assert not n["HomeNet"].open
    assert "company" in n["Office"].unsupported.lower() and "WEP" in n["OldRouter"].unsupported
    assert n["Hidden net"].signal == 0


def test_wpa3_only_uses_sae_and_everything_else_wpa_psk():
    n = by_name()
    assert n["Modern"].key_mgmt == "sae"
    assert n["Mixed"].key_mgmt == "wpa-psk" and n["HomeNet"].key_mgmt == "wpa-psk"


def test_terse_split_unescapes_colons_and_backslashes():
    assert net.split_terse("a\\:b:c\\\\d::") == ["a:b", "c\\d", "", ""]


@pytest.mark.parametrize("text, ok", [
    ("wifi:disconnected\nethernet:connected\n", True), ("wifi:unmanaged\n", False), ("wifi:unavailable\n", False),
    ("ethernet:connected\nloopback:unmanaged\n", False), ("", False),
])
def test_wifi_device_detection(text, ok):
    assert net.has_wifi_device(text) is ok


@pytest.mark.parametrize("pw, ok", [("12345678", True), ("x" * 63, True), ("x" * 64, False), ("a" * 64, True),
                                    ("short", False), ("", False), ("good password\nextra", False),
                                    ("pass\x00word1", False)])
def test_password_rules(pw, ok):
    assert (net.password_problem(pw) is None) is ok


@pytest.mark.parametrize("ssid, ok", [("Home", True), ("", False), ("x" * 32, True), ("x" * 33, False),
                                      ("a\nb", False), ("ü" * 16, True), ("ü" * 17, False)])
def test_ssid_rules(ssid, ok):
    assert (net.ssid_problem(ssid) is None) is ok


class Fake:
    """Records every nmcli call; fails the call whose words match `fail`. Reads the secret file while it exists."""

    def __init__(self, fail=None, err="Error: boom"):
        self.calls, self.secret_seen, self.fail, self.err = [], None, fail, err

    def __call__(self, argv, **_kw):
        self.calls.append(argv)
        if "passwd-file" in argv:
            path = argv[argv.index("passwd-file") + 1]
            self.secret_seen = (path, open(path).read(), oct(__import__("os").stat(path).st_mode & 0o777))
        bad = self.fail and self.fail in " ".join(argv)
        return subprocess.CompletedProcess(argv, 1 if bad else 0, "", self.err if bad else "")


def test_a_secured_join_adds_the_profile_then_activates_it_with_a_secret_file(tmp_path):
    fake = Fake()
    ok, msg = net.join(by_name()["HomeNet"], "hunter2hunter2", fake, tmp_path)
    assert ok and msg == "Connected to HomeNet."
    add, up = fake.calls
    assert add[:6] == ["nmcli", "connection", "add", "type", "wifi", "con-name"]
    assert "wifi-sec.key-mgmt" in add and add[add.index("wifi-sec.key-mgmt") + 1] == "wpa-psk"
    assert up[:5] == ["nmcli", "connection", "up", "id", "HomeNet"]
    path, text, mode = fake.secret_seen
    assert text == "802-11-wireless-security.psk:hunter2hunter2\n" and mode == "0o600"
    assert not any("hunter2" in a for argv in fake.calls for a in argv)  # never in an argument list
    assert not list(tmp_path.iterdir())  # the secret file is gone


def test_an_open_network_needs_no_password_and_no_file(tmp_path):
    fake = Fake()
    ok, _ = net.join(by_name()["Cafe: Free"], "", fake, tmp_path)
    assert ok and fake.calls == [["nmcli", "device", "wifi", "connect", "Cafe: Free"]] and fake.secret_seen is None


def test_a_failed_join_removes_the_profile_and_the_secret_and_explains(tmp_path):
    err = "Error: Connection activation failed: Secrets were required, but not provided."
    fake = Fake(fail="connection up", err=err)
    ok, msg = net.join(by_name()["HomeNet"], "hunter2hunter2", fake, tmp_path)
    assert not ok and msg == "That password was not accepted."
    assert fake.calls[-1] == ["nmcli", "connection", "delete", "id", "HomeNet"]
    assert not list(tmp_path.iterdir())


def test_nothing_runs_for_an_unsupported_network_or_a_bad_password(tmp_path):
    fake = Fake()
    assert not net.join(by_name()["Office"], "hunter2hunter2", fake, tmp_path)[0]
    assert not net.join(by_name()["HomeNet"], "short", fake, tmp_path)[0]
    assert fake.calls == []


def test_nmcli_missing_or_slow_is_reported_not_raised(tmp_path):
    def missing(argv, **_kw):
        raise FileNotFoundError(2, "No such file", "nmcli")

    def slow(argv, **_kw):
        raise subprocess.TimeoutExpired(argv, 60)

    assert "nmcli" in net.join(by_name()["Cafe: Free"], "", missing, tmp_path)[1]
    assert "in time" in net.join(by_name()["HomeNet"], "hunter2hunter2", slow, tmp_path)[1]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("text, said", [
    ("Error: No network with SSID 'x' found.", "out of range"),
    ("Error: Timeout expired (90 seconds)", "in time"),
    ("Error: Not authorized to control networking.", "may not change"),
    ("something odd\nlast line here\n", "last line here"), ("", "Could not join"),
])
def test_error_texts_become_one_readable_line(text, said):
    assert said in net.friendly(text)
