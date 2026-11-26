import pytest

from jev_android_mcp.gesture import plan_gesture
from jev_android_mcp.models import Bounds

BOUNDS = Bounds(left=100, top=200, right=1101, bottom=1201)


def test_move_interpolates_frames_between_down_and_up() -> None:
    commands, total_ms = plan_gesture(
        BOUNDS,
        [{"down": [0, 0]}, {"move": [1, 1], "ms": 48}, {"up": True}],
    )

    assert commands == [
        ["input", "motionevent", "DOWN", "100", "200"],
        ["input", "motionevent", "MOVE", "433", "533"],
        ["sleep", "0.016"],
        ["input", "motionevent", "MOVE", "767", "867"],
        ["sleep", "0.016"],
        ["input", "motionevent", "MOVE", "1100", "1200"],
        ["input", "motionevent", "UP", "1100", "1200"],
    ]
    assert total_ms == 48


def test_hold_tap_and_wheel_map_to_input_commands() -> None:
    commands, total_ms = plan_gesture(
        BOUNDS,
        [{"tap": [0.5, 0.5]}, {"hold": 250}, {"wheel": -3, "at": [0, 1]}],
    )

    assert commands == [
        ["input", "motionevent", "DOWN", "600", "700"],
        ["sleep", "0.040"],
        ["input", "motionevent", "UP", "600", "700"],
        ["sleep", "0.250"],
        ["input", "mouse", "scroll", "100", "1200", "--axis", "VSCROLL,-3"],
    ]
    assert total_ms == 290


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ([], "non-empty"),
        ([{"down": [0, 0]}], "end with the finger up"),
        ([{"move": [0, 0]}], "needs the finger down"),
        ([{"up": True}], "already up"),
        ([{"down": [0, 0]}, {"down": [0, 0]}, {"up": True}], "already down"),
        ([{"down": [0, 0]}, {"tap": [0, 0]}, {"up": True}], "tap needs the finger up"),
        ([{"tap": [0, 1.2]}], "fractions"),
        ([{"tap": [0]}], "fractions"),
        ([{"hold": 6000}], "0 to 5000"),
        ([{"hold": True}], "0 to 5000"),
        ([{"hold": 4000}, {"hold": 4000}, {"hold": 4000}], "limit is 10000"),
        ([{"wheel": 0}], "non-zero"),
        ([{"wheel": 99}], "non-zero"),
        ([{"swipe": [0, 0]}], "needs one of"),
        (["down"], "must be an object"),
        ([{"tap": [0.5, 0.5]}] * 51, "more than 50"),
    ],
)
def test_rejects_invalid_gestures(steps, message) -> None:
    with pytest.raises(ValueError, match=message):
        plan_gesture(BOUNDS, steps)
