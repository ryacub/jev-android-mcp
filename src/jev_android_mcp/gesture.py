"""Turn a bounded list of gesture steps into Android `input` commands."""

from __future__ import annotations

from typing import Any

from .models import Bounds

FRAME_MS = 16
TAP_MS = 40
MAX_STEPS = 50
MAX_STEP_MS = 5000
MAX_TOTAL_MS = 10000
MAX_WHEEL = 50

STEP_KINDS = ("down", "move", "hold", "up", "tap", "wheel")

Command = list[str]


def _fraction_point(bounds: Bounds, value: Any, name: str) -> tuple[int, int]:
    if (
        not isinstance(value, list | tuple)
        or len(value) != 2
        or not all(isinstance(v, int | float) and 0.0 <= v <= 1.0 for v in value)
    ):
        raise ValueError(f"{name} must be [x, y] fractions between 0 and 1")
    x = bounds.left + round(value[0] * (bounds.right - bounds.left - 1))
    y = bounds.top + round(value[1] * (bounds.bottom - bounds.top - 1))
    return x, y


def _duration(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_STEP_MS:
        raise ValueError(f"{name} must be an integer from 0 to {MAX_STEP_MS} ms")
    return value


def _sleep(ms: int) -> Command:
    return ["sleep", f"{ms / 1000:.3f}"]


def _motion(kind: str, point: tuple[int, int]) -> Command:
    return ["input", "motionevent", kind, str(point[0]), str(point[1])]


def plan_gesture(bounds: Bounds, steps: list[dict[str, Any]]) -> tuple[list[Command], int]:
    """Return shell commands for the steps and the planned duration in ms.

    Each step has one key. Points are [x, y] fractions of the node bounds.
    - {"down": [x, y]} puts one finger down.
    - {"move": [x, y], "ms": 300} moves the finger in 16 ms frames.
    - {"hold": ms} waits, with the finger down or up.
    - {"up": true} lifts the finger where it is.
    - {"tap": [x, y]} taps once with the finger up.
    - {"wheel": n, "at": [x, y]} sends n mouse-wheel notches; negative n
      reveals content below.
    """

    if not isinstance(steps, list) or not steps:
        raise ValueError("gesture must be a non-empty list of steps")
    if len(steps) > MAX_STEPS:
        raise ValueError(f"gesture has more than {MAX_STEPS} steps")

    commands: list[Command] = []
    total_ms = 0
    pointer: tuple[int, int] | None = None
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"step {index} must be an object")
        kind = next((key for key in STEP_KINDS if key in step), None)
        if kind is None:
            raise ValueError(f"step {index} needs one of down, move, hold, up, tap, wheel")
        if kind == "down":
            if pointer is not None:
                raise ValueError(f"step {index}: the finger is already down")
            pointer = _fraction_point(bounds, step["down"], f"step {index} down")
            commands.append(_motion("DOWN", pointer))
        elif kind == "move":
            if pointer is None:
                raise ValueError(f"step {index}: move needs the finger down")
            target = _fraction_point(bounds, step["move"], f"step {index} move")
            ms = _duration(step.get("ms", 300), f"step {index} ms")
            frames = max(1, ms // FRAME_MS)
            start = pointer
            for frame in range(1, frames + 1):
                point = (
                    round(start[0] + (target[0] - start[0]) * frame / frames),
                    round(start[1] + (target[1] - start[1]) * frame / frames),
                )
                commands.append(_motion("MOVE", point))
                if frame < frames:
                    commands.append(_sleep(ms // frames))
            pointer = target
            total_ms += ms
        elif kind == "hold":
            ms = _duration(step["hold"], f"step {index} hold")
            commands.append(_sleep(ms))
            total_ms += ms
        elif kind == "up":
            if pointer is None:
                raise ValueError(f"step {index}: the finger is already up")
            commands.append(_motion("UP", pointer))
            pointer = None
        elif kind == "tap":
            if pointer is not None:
                raise ValueError(f"step {index}: tap needs the finger up")
            point = _fraction_point(bounds, step["tap"], f"step {index} tap")
            commands.extend([_motion("DOWN", point), _sleep(TAP_MS), _motion("UP", point)])
            total_ms += TAP_MS
        else:
            notches = step["wheel"]
            if (
                not isinstance(notches, int)
                or isinstance(notches, bool)
                or notches == 0
                or abs(notches) > MAX_WHEEL
            ):
                raise ValueError(f"step {index} wheel must be a non-zero integer up to {MAX_WHEEL}")
            point = _fraction_point(bounds, step.get("at", [0.5, 0.5]), f"step {index} at")
            commands.append(
                [
                    "input",
                    "mouse",
                    "scroll",
                    str(point[0]),
                    str(point[1]),
                    "--axis",
                    f"VSCROLL,{notches}",
                ]
            )
    if pointer is not None:
        raise ValueError("gesture must end with the finger up")
    if total_ms > MAX_TOTAL_MS:
        raise ValueError(f"gesture lasts {total_ms} ms; the limit is {MAX_TOTAL_MS} ms")
    return commands, total_ms
