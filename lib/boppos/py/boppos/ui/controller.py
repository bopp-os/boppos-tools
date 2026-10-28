"""Gamepad input: SDL3 (already in the image through gamescope) over ctypes, mapped to UI actions.

Everything except `Sdl3Gamepads` is pure so it can be tested without a controller or a display.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import enum
import struct


class Action(enum.Enum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    ACCEPT = "accept"
    BACK = "back"
    PREV = "prev"
    NEXT = "next"
    MENU = "menu"
    PAGE_UP = "page-up"
    PAGE_DOWN = "page-down"
    TOP = "top"
    BOTTOM = "bottom"


DIRECTIONS = (Action.UP, Action.DOWN, Action.LEFT, Action.RIGHT)
PAGES = (Action.PAGE_UP, Action.PAGE_DOWN)

# SDL_GamepadButton (positional: SOUTH is the bottom face button on every layout)
BUTTON_ACTIONS = {
    0: Action.ACCEPT,  # SOUTH (A)
    1: Action.BACK,  # EAST (B)
    6: Action.MENU,  # START
    9: Action.PREV,  # LEFT_SHOULDER
    10: Action.NEXT,  # RIGHT_SHOULDER
    11: Action.UP,
    12: Action.DOWN,
    13: Action.LEFT,
    14: Action.RIGHT,
}
AXIS_LEFTX, AXIS_LEFTY, AXIS_RIGHTX, AXIS_RIGHTY, AXIS_LTRIG, AXIS_RTRIG = 0, 1, 2, 3, 4, 5
STICK_THRESHOLD = 16000  # of 32767
STICK_RELEASE = 9000  # hysteresis so a stick resting near the edge does not flutter
DOMINANCE = 1.6  # to start moving, one axis must beat the other by this factor: diagonals do nothing
KEEP = 1.15  # once moving, drift on the other axis may grow this far before the direction can change
PAGE_THRESHOLD, PAGE_RELEASE = 18000, 10000  # the right stick flicks up/down to jump a page
TRIGGER_THRESHOLD, TRIGGER_RELEASE = 16000, 8000

# SDL3 event types
EV_AXIS, EV_BUTTON_DOWN, EV_BUTTON_UP, EV_ADDED, EV_REMOVED = 0x650, 0x651, 0x652, 0x653, 0x654
SDL_INIT_GAMEPAD = 0x2000
EVENT_SIZE = 128


def decode_event(buf: bytes) -> tuple | None:
    """SDL_Event bytes -> ("button", id, down, which) | ("axis", id, value, which) | ("added"/"removed", which).

    `which` is the SDL joystick id of the device that produced the event.
    """
    (etype,) = struct.unpack_from("<I", buf, 0)
    if etype in (EV_BUTTON_DOWN, EV_BUTTON_UP):
        (which,) = struct.unpack_from("<I", buf, 16)
        return ("button", buf[20], etype == EV_BUTTON_DOWN, which)
    if etype == EV_AXIS:
        (which,) = struct.unpack_from("<I", buf, 16)
        (value,) = struct.unpack_from("<h", buf, 24)
        return ("axis", buf[20], value, which)
    if etype in (EV_ADDED, EV_REMOVED):
        (which,) = struct.unpack_from("<I", buf, 16)
        return ("added" if etype == EV_ADDED else "removed", which)
    return None


def stick_direction(x: int, y: int, current: Action | None) -> Action | None:
    """Direction of a stick, or None. Judged from both axes together so that a slight sideways drift while
    pushing down is not read as Left or Right: the dominant axis wins, and a diagonal does nothing."""
    engaged = current is not None
    ax, ay = abs(x), abs(y)
    if max(ax, ay) < (STICK_RELEASE if engaged else STICK_THRESHOLD):
        return None
    horizontal = ax >= ay
    major, minor = (ax, ay) if horizontal else (ay, ax)
    if major < (KEEP if engaged else DOMINANCE) * minor:
        return current if engaged else None  # ambiguous: keep what we had, or stay still
    if horizontal:
        return Action.LEFT if x < 0 else Action.RIGHT
    return Action.UP if y < 0 else Action.DOWN


def page_direction(x: int, y: int, current: Action | None) -> Action | None:
    """Right stick: only a clear up or down flick counts."""
    engaged = current is not None
    if abs(y) < (PAGE_RELEASE if engaged else PAGE_THRESHOLD) or abs(y) < 1.2 * abs(x):
        return None
    return Action.PAGE_UP if y < 0 else Action.PAGE_DOWN


class Repeater:
    """Turns held directions into a first press plus timed repeats (like key auto-repeat).

    Held directions speed up the longer they are held, so a long list does not take forever; page jumps
    repeat at a steady, slower rate so a flick does not fly past what you wanted.
    """

    ACCEL = ((1.0, 0.07), (2.0, 0.045))  # (held for this long, repeat every this often)

    def __init__(self, delay: float = 0.40, interval: float = 0.11, page_delay: float = 0.35,
                 page_interval: float = 0.28):
        self.delay, self.interval = delay, interval
        self.page_delay, self.page_interval = page_delay, page_interval
        self._held: dict[Action, list[float]] = {}  # action -> [time of next repeat, time first pressed]

    def press(self, action: Action, now: float) -> Action:
        self._held[action] = [now + (self.page_delay if action in PAGES else self.delay), now]
        return action

    def release(self, action: Action) -> None:
        self._held.pop(action, None)

    def release_all(self) -> None:
        self._held.clear()

    def _interval(self, action: Action, held_for: float) -> float:
        if action in PAGES:
            return self.page_interval
        interval = self.interval
        for after, faster in self.ACCEL:
            if held_for >= after:
                interval = faster
        return interval

    def tick(self, now: float) -> list[Action]:
        out = []
        for action, (due, started) in list(self._held.items()):
            if now >= due:
                out.append(action)
                self._held[action][0] = now + self._interval(action, now - started)
        return out


class InputMapper:
    """Feeds decoded events in, gets UI actions out. Directions and page jumps repeat while held."""

    def __init__(self, repeater: Repeater | None = None):
        self.rep = repeater or Repeater()
        self._dpad_held: set[Action] = set()
        self._left = [0, 0]
        self._left_dir: Action | None = None
        self._right = [0, 0]
        self._page_src: dict[str, Action | None] = {"stick": None, "lt": None, "rt": None}

    def _release_direction(self, action: Action) -> None:
        if action not in self._dpad_held and action != self._left_dir:
            self.rep.release(action)

    def feed(self, ev: tuple, now: float) -> list[Action]:
        kind = ev[0]
        if kind == "button":
            action = BUTTON_ACTIONS.get(ev[1])
            if action is None:
                return []
            down = ev[2]
            if action in DIRECTIONS:
                if down:
                    self._dpad_held.add(action)
                    return [self.rep.press(action, now)]
                self._dpad_held.discard(action)
                self._release_direction(action)
                return []
            return [action] if down else []
        if kind == "axis":
            return self._axis(ev[1], ev[2], now)
        return []

    def _axis(self, axis: int, value: int, now: float) -> list[Action]:
        if axis in (AXIS_LEFTX, AXIS_LEFTY):
            self._left[0 if axis == AXIS_LEFTX else 1] = value
            new = stick_direction(self._left[0], self._left[1], self._left_dir)
            old, self._left_dir = self._left_dir, new
            if new == old:
                return []
            if old is not None:
                self._release_direction(old)
            return [self.rep.press(new, now)] if new is not None else []
        if axis in (AXIS_RIGHTX, AXIS_RIGHTY):
            self._right[0 if axis == AXIS_RIGHTX else 1] = value
            return self._set_page("stick", page_direction(self._right[0], self._right[1],
                                                            self._page_src["stick"]), now)
        if axis in (AXIS_LTRIG, AXIS_RTRIG):
            source, action = ("lt", Action.PAGE_UP) if axis == AXIS_LTRIG else ("rt", Action.PAGE_DOWN)
            engaged = self._page_src[source] is not None
            held = value >= (TRIGGER_RELEASE if engaged else TRIGGER_THRESHOLD)
            return self._set_page(source, action if held else None, now)
        return []

    def _set_page(self, source: str, new: Action | None, now: float) -> list[Action]:
        old = self._page_src[source]
        if new == old:
            return []
        self._page_src[source] = new
        if old is not None and old not in self._page_src.values():
            self.rep.release(old)
        return [self.rep.press(new, now)] if new is not None else []

    def tick(self, now: float) -> list[Action]:
        return self.rep.tick(now)

    def stop_repeats(self) -> None:
        """Forget held directions and page jumps, so nothing repeats until it is pressed again."""
        self.rep.release_all()


class Sdl3Gamepads:
    """Minimal SDL3 gamepad event source. Raises OSError when SDL3 is not available."""

    def __init__(self) -> None:
        name = ctypes.util.find_library("SDL3") or "libSDL3.so.0"
        self.lib = ctypes.CDLL(name)
        lib = self.lib
        lib.SDL_Init.argtypes, lib.SDL_Init.restype = [ctypes.c_uint32], ctypes.c_bool
        lib.SDL_PollEvent.argtypes, lib.SDL_PollEvent.restype = [ctypes.c_void_p], ctypes.c_bool
        lib.SDL_OpenGamepad.argtypes, lib.SDL_OpenGamepad.restype = [ctypes.c_uint32], ctypes.c_void_p
        lib.SDL_CloseGamepad.argtypes = [ctypes.c_void_p]
        lib.SDL_GetGamepads.argtypes = [ctypes.POINTER(ctypes.c_int)]
        lib.SDL_GetGamepads.restype = ctypes.POINTER(ctypes.c_uint32)
        lib.SDL_free.argtypes = [ctypes.c_void_p]
        # SDL_GetRealGamepadTypeForID ignores mapping overrides; older SDL3 builds only have the plain one
        for name in ("SDL_GetRealGamepadTypeForID", "SDL_GetGamepadTypeForID"):
            fn = getattr(lib, name, None)
            if fn is not None:
                fn.argtypes, fn.restype = [ctypes.c_uint32], ctypes.c_int
                self._type_fn = fn
                break
        else:
            self._type_fn = None
        lib.SDL_SetHint.argtypes, lib.SDL_SetHint.restype = [ctypes.c_char_p, ctypes.c_char_p], ctypes.c_bool
        lib.SDL_Quit.argtypes = []
        lib.SDL_SetHint(b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", b"1")
        if not lib.SDL_Init(SDL_INIT_GAMEPAD):
            raise OSError("SDL_Init(GAMEPAD) failed")
        self._open: dict[int, int] = {}
        count = ctypes.c_int(0)
        ids = lib.SDL_GetGamepads(ctypes.byref(count))
        if ids:
            for i in range(count.value):
                self._open_pad(ids[i])
            lib.SDL_free(ids)
        self._buf = ctypes.create_string_buffer(EVENT_SIZE)

    def _open_pad(self, jid: int) -> None:
        if jid not in self._open:
            handle = self.lib.SDL_OpenGamepad(jid)
            if handle:
                self._open[jid] = handle

    @property
    def connected(self) -> int:
        return len(self._open)

    def family(self, which: int | None = None) -> str:
        """playstation, xbox, nintendo or generic for a device id (default: the first connected pad)."""
        from .hints import family_for_type

        jid = which if which is not None else next(iter(self._open), None)
        if jid is None or self._type_fn is None:
            return "generic"
        return family_for_type(self._type_fn(jid))

    def poll(self) -> list[tuple]:
        out = []
        while self.lib.SDL_PollEvent(ctypes.byref(self._buf)):
            ev = decode_event(self._buf.raw)
            if ev is None:
                continue
            if ev[0] == "added":
                self._open_pad(ev[1])
            elif ev[0] == "removed":
                handle = self._open.pop(ev[1], None)
                if handle:
                    self.lib.SDL_CloseGamepad(handle)
            out.append(ev)  # hotplug events are passed on too, so the UI can update its prompts
        return out

    def close(self) -> None:
        for handle in self._open.values():
            self.lib.SDL_CloseGamepad(handle)
        self._open.clear()
        self.lib.SDL_Quit()
