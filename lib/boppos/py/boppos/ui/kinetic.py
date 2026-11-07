"""Drag-to-scroll math, GTK-free (the GTK side is DeckWindow's drag gesture in ui/app.py).

gamescope hands a touchscreen to Xwayland apps as a mouse: a tap is a click, and a finger drag is a mouse drag,
which GtkScrolledWindow ignores (its own drag scrolling is for touch devices only). So in Game Mode a primary-button
drag scrolls, the way GTK's touch scrolling does: nothing happens until the pointer has moved past a threshold (so
a tap still clicks), then the content follows the finger, and a quick flick keeps going for a moment.
"""

from __future__ import annotations

THRESHOLD = 12.0  # px: below this a press is a tap (an emulated touch jitters more than a mouse)
FRICTION = 4.0  # 1/s: how fast a fling slows down (velocity *= exp(-FRICTION * dt))
MIN_FLING = 120.0  # px/s: a release slower than this stops at once
MAX_FLING = 6000.0  # px/s
SAMPLE_WINDOW = 0.1  # s: the velocity at release is measured over the last tenth of a second


def past_threshold(dx: float, dy: float, threshold: float = THRESHOLD) -> bool:
    return dx * dx + dy * dy >= threshold * threshold


def clamp_value(value: float, lower: float, upper: float, page: float) -> float:
    """An adjustment's value kept in range: [lower, upper - page]."""
    return max(lower, min(value, max(lower, upper - page)))


class Fling:
    """Velocity tracking during the drag, then the decaying scroll after release."""

    def __init__(self) -> None:
        self.samples: list[tuple[float, float]] = []  # (time, offset_y)
        self.velocity = 0.0  # content px/s; positive scrolls down (the finger moved up)

    def track(self, now: float, offset_y: float) -> None:
        self.samples.append((now, offset_y))
        while len(self.samples) > 2 and now - self.samples[0][0] > SAMPLE_WINDOW:
            self.samples.pop(0)

    def release(self, now: float) -> float:
        """The fling velocity at release (0 when too slow to keep going)."""
        recent = [(t, y) for t, y in self.samples if now - t <= SAMPLE_WINDOW * 1.5]
        self.samples = []
        if len(recent) < 2 or recent[-1][0] <= recent[0][0]:
            self.velocity = 0.0
            return 0.0
        v = -(recent[-1][1] - recent[0][1]) / (recent[-1][0] - recent[0][0])
        v = max(-MAX_FLING, min(MAX_FLING, v))
        self.velocity = v if abs(v) >= MIN_FLING else 0.0
        return self.velocity

    def step(self, dt: float) -> float:
        """How far to scroll in the next dt seconds; the velocity decays. 0 once it has stopped."""
        import math

        if self.velocity == 0.0 or dt <= 0:
            return 0.0
        decay = math.exp(-FRICTION * dt)
        moved = self.velocity * (1 - decay) / FRICTION
        self.velocity *= decay
        if abs(self.velocity) < MIN_FLING / 4:
            self.velocity = 0.0
        return moved

    def stop(self) -> None:
        self.velocity = 0.0
        self.samples = []
