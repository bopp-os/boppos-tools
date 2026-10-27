from boppos.tray_logic import SEP, compute_state, last_checked_label, menu_spec, tooltip

OK = {"update_available": False, "checked_at": "2026-10-26T12:30:00Z", "diff": None, "error": None}
UPD = {**OK, "update_available": True, "diff": "+ pkg"}


def ids(spec):
    return [None if e is None else e["id"] for e in spec]


def test_states():
    assert compute_state(OK, None).kind == "ok"
    assert compute_state(UPD, None).kind == "update" and compute_state(UPD, None).needs_attention
    assert compute_state(None, None).kind == "unknown"
    assert compute_state({**OK, "error": "boom"}, None).kind == "unknown"
    # a staged deployment needs a restart, whatever the status file says
    assert compute_state(OK, {"reboot_required": True}).kind == "restart"
    assert compute_state(UPD, {"reboot_required": True}).kind == "restart"
    assert compute_state(UPD, {"reboot_required": False}).kind == "update"


def test_an_available_update_with_an_error_field_is_still_an_update():
    assert compute_state({**UPD, "error": "partial"}, None).kind == "update"


def test_last_checked_label():
    assert last_checked_label(None) == "Last checked: never"
    assert last_checked_label({"checked_at": "garbage"}) == "Last checked: never"
    assert last_checked_label(OK).startswith("Last checked: ")


def test_menu_order_and_names():
    spec = menu_spec(compute_state(OK, None), have_settings=True, have_system=True, busy=False)
    assert ids(spec) == ["status", SEP, "open-settings", SEP, "check", "install", "review", SEP, "last-checked",
                         "quit"]
    labels = [e["label"] for e in spec if e]
    assert labels[1:5] == ["Open BoppOS Settings", "Check for Updates", "Install Updates", "Review Changes…"]
    install = [e for e in spec if e and e["id"] == "install"][0]["children"]
    assert [e["label"] for e in install if e] == [
        "Everything (system, apps, containers)", "Operating System", "Flatpak Apps", "Firmware",
        "Containers (Distrobox & Litterbox)…"]


def test_restart_item_only_when_a_restart_is_needed_and_comes_first():
    ok = menu_spec(compute_state(OK, None), have_settings=True, have_system=True, busy=False)
    assert "restart" not in ids(ok)
    pending = menu_spec(compute_state(OK, {"reboot_required": True}), have_settings=True, have_system=True,
                        busy=False)
    assert ids(pending)[:2] == ["status", "restart"]


def test_settings_entry_hidden_without_boppos_deck_and_no_dangling_separators():
    spec = menu_spec(compute_state(OK, None), have_settings=False, have_system=True, busy=False)
    assert [e["visible"] for e in spec if e and e["id"] == "open-settings"] == [False]
    assert spec[0] is not None and spec[-1] is not None
    assert all(not (a is None and b is None) for a, b in zip(spec, spec[1:], strict=False))
    default = [e for e in menu_spec(compute_state(OK, None), have_settings=True, have_system=True, busy=False)
               if e and e["default"]]
    assert [e["id"] for e in default] == ["open-settings"]  # left click opens the app


def test_busy_disables_updating_and_renames_it():
    spec = menu_spec(compute_state(UPD, None), have_settings=True, have_system=True, busy=True)
    by = {e["id"]: e for e in spec if e}
    assert by["check"]["enabled"] is False
    assert by["install"]["label"] == "Updating…" and by["install"]["enabled"] is False


def test_review_changes_needs_a_diff():
    by = {e["id"]: e for e in menu_spec(compute_state(OK, None), have_settings=True, have_system=True, busy=False)
          if e}
    assert by["review"]["enabled"] is False
    by = {e["id"]: e for e in menu_spec(compute_state(UPD, None), have_settings=True, have_system=True, busy=False)
          if e}
    assert by["review"]["enabled"] is True


def test_without_bopp_system_only_everything_and_containers_remain():
    spec = menu_spec(compute_state(OK, None), have_settings=True, have_system=False, busy=False)
    install = [e for e in spec if e and e["id"] == "install"][0]["children"]
    assert [e["id"] for e in install if e and e["visible"]] == ["update-all", "update-containers"]


def test_tooltip():
    assert "restart" in tooltip(compute_state(OK, {"reboot_required": True}))
    assert "unavailable" in tooltip(compute_state(None, None))
