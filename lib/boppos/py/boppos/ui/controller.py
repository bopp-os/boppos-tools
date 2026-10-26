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


DIRECTIONS = (Action.UP, Action.DOWN, Action.LEFT, Action.RIGHT)

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
AXIS_LEFTX, AXIS_LEFTY = 0, 1
STICK_THRESHOLD = 16000  # of 32767
STICK_RELEASE = 9000  # hysteresis so a stick resting near the edge does not flutter

# SDL3 event types
EV_AXIS, EV_BUTTON_DOWN, EV_BUTTON_UP, EV_ADDED, EV_REMOVED = 0x650, 0x651, 0x652, 0x653, 0x654
SDL_INIT_GAMEPAD = 0x2000
EVENT_SIZE = 128


def decode_event(buf: bytes) -> tuple | None:
    """SDL_Event bytes -> ("button", id, down) | ("axis", id, value) | ("added"/"removed", joystick_id)."""
    (etype,) = struct.unpack_from("<I", buf, 0)
    if etype in (EV_BUTTON_DOWN, EV_BUTTON_UP):
        button = buf[20]
        return ("button", button, etype == EV_BUTTON_DOWN)
    if etype == EV_AXIS:
        axis = buf[20]
        (value,) = struct.unpack_from("<h", buf, 24)
        return ("axis", axis, value)
    if etype in (EV_ADDED, EV_REMOVED):
        (which,) = struct.unpack_from("<I", buf, 16)
        return ("added" if etype == EV_ADDED else "removed", which)
    return None


class Repeater:
    """Turns held directions into a first press plus timed repeats (like key auto-repeat)."""

    def __init__(self, delay: float = 0.40, interval: float = 0.11):
        self.delay, self.interval = delay, interval
        self._held: dict[Action, float] = {}  # action -> time of next repeat

    def press(self, action: Action, now: float) -> Action:
        self._held[action] = now + self.delay
        return action

    def release(self, action: Action) -> None:
        self._held.pop(action, None)

    def tick(self, now: float) -> list[Action]:
        out = []
        for action, due in list(self._held.items()):
            if now >= due:
                out.append(action)
                self._held[action] = now + self.interval
        return out


class InputMapper:
    """Feeds decoded events in, gets UI actions out. Direction actions repeat while held."""

    def __init__(self, repeater: Repeater | None = None):
        self.rep = repeater or Repeater()
        self._stick: dict[int, Action | None] = {AXIS_LEFTX: None, AXIS_LEFTY: None}
        self._dpad_held: set[Action] = set()

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
                if action not in self._stick.values():
                    self.rep.release(action)
                return []
            return [action] if down else []
        if kind == "axis" and ev[1] in self._stick:
            return self._axis(ev[1], ev[2], now)
        return []

    def _axis(self, axis: int, value: int, now: float) -> list[Action]:
        neg, pos = (Action.LEFT, Action.RIGHT) if axis == AXIS_LEFTX else (Action.UP, Action.DOWN)
        cur = self._stick[axis]
        want: Action | None = cur
        if abs(value) < STICK_RELEASE:
            want = None
        elif value <= -STICK_THRESHOLD:
            want = neg
        elif value >= STICK_THRESHOLD:
            want = pos
        if want == cur:
            return []
        self._stick[axis] = want
        if cur is not None and cur not in self._dpad_held:
            self.rep.release(cur)
        return [self.rep.press(want, now)] if want is not None else []

    def tick(self, now: float) -> list[Action]:
        return self.rep.tick(now)


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
            else:
                out.append(ev)
        return out

    def close(self) -> None:
        for handle in self._open.values():
            self.lib.SDL_CloseGamepad(handle)
        self._open.clear()
        self.lib.SDL_Quit()
