import json
import struct

from boppos.ui import backend
from boppos.ui.controller import (
    AXIS_LEFTX,
    AXIS_LEFTY,
    EV_AXIS,
    EV_BUTTON_DOWN,
    EV_BUTTON_UP,
    Action,
    InputMapper,
    Repeater,
    decode_event,
)


def button_event(down: bool, button: int, which: int = 7) -> bytes:
    buf = bytearray(128)
    struct.pack_into("<I", buf, 16, which)
    struct.pack_into("<I", buf, 0, EV_BUTTON_DOWN if down else EV_BUTTON_UP)
    buf[20] = button
    buf[21] = int(down)
    return bytes(buf)


def axis_event(axis: int, value: int, which: int = 7) -> bytes:
    buf = bytearray(128)
    struct.pack_into("<I", buf, 16, which)
    struct.pack_into("<I", buf, 0, EV_AXIS)
    buf[20] = axis
    struct.pack_into("<h", buf, 24, value)
    return bytes(buf)


def test_decode_events():
    assert decode_event(button_event(True, 0)) == ("button", 0, True, 7)
    assert decode_event(button_event(False, 10, which=3)) == ("button", 10, False, 3)
    assert decode_event(axis_event(1, -20000, which=9)) == ("axis", 1, -20000, 9)
    assert decode_event(bytes(128)) is None


def test_face_and_shoulder_buttons():
    m = InputMapper()
    assert m.feed(("button", 0, True, 1), 0) == [Action.ACCEPT]
    assert m.feed(("button", 0, False, 1), 0) == []
    assert m.feed(("button", 1, True, 1), 0) == [Action.BACK]
    assert m.feed(("button", 9, True, 1), 0) == [Action.PREV]
    assert m.feed(("button", 10, True, 1), 0) == [Action.NEXT]
    assert m.feed(("button", 2, True, 1), 0) == []  # X is unused


def test_dpad_repeats_while_held_then_stops():
    m = InputMapper(Repeater(delay=0.4, interval=0.1))
    assert m.feed(("button", 12, True, 1), 0.0) == [Action.DOWN]
    assert m.tick(0.3) == []
    assert m.tick(0.41) == [Action.DOWN]
    assert m.tick(0.52) == [Action.DOWN]
    m.feed(("button", 12, False, 1), 0.6)
    assert m.tick(2.0) == []


def test_stop_repeats_keeps_a_held_stick_from_firing_later():
    # what the window does while it is in the background: keep reading, act on nothing
    m = InputMapper(Repeater(delay=0.4, interval=0.1))
    m.feed(("button", 12, True, 1), 0.0)
    m.feed(("axis", AXIS_LEFTX, 30000, 1), 0.0)
    m.stop_repeats()
    assert m.tick(5.0) == []
    assert m.feed(("axis", AXIS_LEFTX, 30000, 1), 5.0) == []  # still held: not a new push
    assert m.feed(("button", 12, False, 1), 5.0) == []
    assert m.feed(("button", 12, True, 1), 5.1) == [Action.DOWN]  # a fresh press works again


def test_stick_has_threshold_and_hysteresis():
    m = InputMapper()
    assert m.feed(("axis", AXIS_LEFTY, 8000, 1), 0) == []  # below threshold
    assert m.feed(("axis", AXIS_LEFTY, 20000, 1), 0) == [Action.DOWN]
    assert m.feed(("axis", AXIS_LEFTY, 12000, 1), 0) == []  # still held (hysteresis)
    assert m.feed(("axis", AXIS_LEFTY, 3000, 1), 0) == []  # released
    assert m.feed(("axis", AXIS_LEFTX, -30000, 1), 0) == [Action.LEFT]
    m.feed(("axis", AXIS_LEFTX, 0, 1), 0)
    assert m.tick(5.0) == []


def test_stick_and_dpad_do_not_cancel_each_other():
    m = InputMapper()
    m.feed(("axis", AXIS_LEFTY, 30000, 1), 0)
    m.feed(("button", 12, True, 1), 0)
    m.feed(("button", 12, False, 1), 0.1)
    assert Action.DOWN in m.tick(5.0)  # the stick is still holding DOWN


def test_parse_event_json_and_plain_text():
    assert backend.parse_event(json.dumps({"event": "done", "ok": True}))["ok"] is True
    assert backend.parse_event("plain output") == {"event": "log", "line": "plain output"}
    assert backend.parse_event('{"no": "event"}')["event"] == "log"


def test_job_collects_events_and_failure(tmp_path):
    out = []
    job = backend.Job(
        [["python3", "-c", "import json;print(json.dumps({'event':'log','line':'hi'}));"
          "print(json.dumps({'event':'done','ok':True,'reboot_required':True}))"],
         ["python3", "-c", "import sys; sys.exit(3)"]],
        deliver=lambda fn: fn(), on_event=out.append, on_finish=lambda ok, final: out.append((ok, final)))
    job._run()
    assert out[0]["line"] == "hi" and out[-1] == (False, {"ok": True, "reboot_required": True})


def test_tool_lookup_override(tmp_path, monkeypatch):
    (tmp_path / "bopp-system").write_text("#!/bin/sh\n")
    monkeypatch.setenv("BOPPOS_DECK_BIN_DIR", str(tmp_path))
    assert backend.tool("bopp-system") == str(tmp_path / "bopp-system")


def test_settings_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert backend.load_settings() == {}
    backend.save_settings({"advanced": True})
    assert backend.load_settings() == {"advanced": True}


def test_gaming_mode_detection():
    assert backend.gaming_mode({"SteamDeck": "1"})
    assert backend.gaming_mode({"GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0"})
    assert not backend.gaming_mode({"HOME": "/x"})


# --- diagonals, page jumps and acceleration -----------------------------------------------------------

from boppos.ui.controller import (  # noqa: E402
    AXIS_LTRIG,
    AXIS_RIGHTX,
    AXIS_RIGHTY,
    AXIS_RTRIG,
    PAGES,
    page_direction,
    stick_direction,
)


def test_a_diagonal_stick_push_does_nothing_but_a_clear_one_moves():
    assert stick_direction(20000, 19000, None) is None  # diagonal: no stray Right
    assert stick_direction(-18000, -17000, None) is None
    assert stick_direction(2000, 20000, None) == Action.DOWN
    assert stick_direction(-25000, 5000, None) == Action.LEFT


def test_drift_while_pushing_down_is_not_read_as_left_or_right():
    m = InputMapper()
    assert m.feed(("axis", AXIS_LEFTY, 22000, 1), 0) == [Action.DOWN]
    for drift in (3000, 8000, 14000, 19000):  # the thumb slides sideways while still pushing down
        assert m.feed(("axis", AXIS_LEFTX, drift, 1), 0) == []
    assert m.tick(0.5) == [Action.DOWN]  # still just repeating Down


def test_a_clear_change_of_direction_still_works():
    m = InputMapper()
    m.feed(("axis", AXIS_LEFTY, 22000, 1), 0)
    m.feed(("axis", AXIS_LEFTY, 0, 1), 0)
    assert m.feed(("axis", AXIS_LEFTX, 25000, 1), 0) == [Action.RIGHT]


def test_right_stick_flicks_page_up_and_down_only_when_clearly_vertical():
    m = InputMapper()
    assert m.feed(("axis", AXIS_RIGHTY, 25000, 1), 0) == [Action.PAGE_DOWN]
    assert m.feed(("axis", AXIS_RIGHTY, 0, 1), 0) == []
    assert m.feed(("axis", AXIS_RIGHTY, -25000, 1), 0) == [Action.PAGE_UP]
    m.feed(("axis", AXIS_RIGHTY, 0, 1), 0)
    assert m.feed(("axis", AXIS_RIGHTX, 25000, 1), 0) == []  # sideways on the right stick: ignored
    assert page_direction(22000, 24000, None) is None  # too diagonal
    assert m.tick(10) == []  # released: nothing keeps repeating


def test_triggers_page_too_and_release_stops_the_repeat():
    m = InputMapper()
    assert m.feed(("axis", AXIS_LTRIG, 30000, 1), 0) == [Action.PAGE_UP]
    assert m.feed(("axis", AXIS_RTRIG, 30000, 1), 0) == [Action.PAGE_DOWN]
    assert m.feed(("axis", AXIS_LTRIG, 20000, 1), 0) == []  # still held (hysteresis)
    m.feed(("axis", AXIS_LTRIG, 0, 1), 0)
    m.feed(("axis", AXIS_RTRIG, 0, 1), 0)
    assert m.tick(10) == []


def test_page_jumps_repeat_steadily_while_directions_speed_up():
    m = InputMapper(Repeater(delay=0.4, interval=0.11, page_delay=0.35, page_interval=0.28))
    m.feed(("axis", AXIS_RIGHTY, 25000, 1), 0)
    pages = [t / 100 for t in range(0, 300) if m.tick(t / 100) == [Action.PAGE_DOWN]]
    gaps = [round(b - a, 2) for a, b in zip(pages, pages[1:], strict=False)]
    assert pages[0] == 0.35 and set(gaps) <= {0.28, 0.29}
    m2 = InputMapper(Repeater(delay=0.4, interval=0.11))
    m2.feed(("button", 12, True, 1), 0)
    ticks = [t / 100 for t in range(0, 330) if m2.tick(t / 100) == [Action.DOWN]]
    early = len([t for t in ticks if t < 1.0])
    late = len([t for t in ticks if 2.0 <= t < 3.0])
    assert late > early * 1.8  # holding longer repeats clearly faster
    assert min(b - a for a, b in zip(ticks, ticks[1:], strict=False)) >= 0.04  # but never absurdly fast


def test_page_actions_are_not_directions():
    assert not set(PAGES) & {Action.UP, Action.DOWN, Action.LEFT, Action.RIGHT}


def test_fingerprint_detects_changes_in_data_and_in_layout_settings():
    same = backend.fingerprint({"a": [1, 2], "b": {"x": 1}}, (False, "{}"))
    assert same == backend.fingerprint({"b": {"x": 1}, "a": [1, 2]}, (False, "{}"))  # key order is irrelevant
    assert same != backend.fingerprint({"a": [1, 3], "b": {"x": 1}}, (False, "{}"))  # the data changed
    assert same != backend.fingerprint({"a": [1, 2], "b": {"x": 1}}, (True, "{}"))   # advanced was switched
    assert backend.fingerprint(object()) is not None  # odd values are stringified, not fatal
