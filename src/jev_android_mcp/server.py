"""Local stdio MCP server for bounded Android emulator operations."""

from __future__ import annotations

import functools
import logging
import os
import re
import time
from collections.abc import Callable
from typing import Any, Literal, TypeVar

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from jev_android_mcp.adb import AdbClient, AdbError
from jev_android_mcp.agent import completion_oracle_for_goal, run_jev
from jev_android_mcp.models import Action, Observation
from jev_android_mcp.state import parse_observation, pinned_window

LOGGER = logging.getLogger(__name__)
PACKAGE_ENV = "ANDROID_APP_PACKAGE"
SYSTEM_PACKAGES = {
    "android",
    "com.android.permissioncontroller",
    "com.google.android.permissioncontroller",
    "com.android.systemui",
    "com.android.documentsui",
    "com.google.android.documentsui",
    "com.google.android.apps.nexuslauncher",
    "com.android.launcher3",
}
# The first tap opens the system PiP menu, whose full-screen button sits at the
# window center and hides after a few seconds. The gap must exceed the double-tap
# timeout, or the system reads a resize gesture.
PIP_MENU_DELAY_SECONDS = 0.6
SCREEN_TEXT_CHARS = 160
SCREEN_TEXT_ITEMS = 80
SCROLL_DIRECTIONS = {"up", "down", "left", "right"}
KEYS = {
    "SPACE",
    "TAB",
    "ESCAPE",
    "DEL",
    "FORWARD_DEL",
    "MOVE_HOME",
    "MOVE_END",
    "PAGE_UP",
    "PAGE_DOWN",
    "DPAD_UP",
    "DPAD_DOWN",
    "DPAD_LEFT",
    "DPAD_RIGHT",
    "DPAD_CENTER",
    "MEDIA_PLAY_PAUSE",
    "MEDIA_NEXT",
    "MEDIA_PREVIOUS",
    "MEDIA_FAST_FORWARD",
    "MEDIA_REWIND",
}
MAX_KEY_REPEAT = 50
URI_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:[^\s]+")
MAX_URI_CHARS = 2048
MAX_WAIT_SECONDS = 120.0
WAIT_POLL_SECONDS = 1.0
Detail = Literal["compact", "full"]
PACKAGE_PATTERN = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$")

mcp = MCPServer(
    "Jev Android MCP",
    instructions=(
        "Use android_observe before android_act. Actions are bound to one "
        "observation ID. android_act supports long press, Enter, Home, fixed "
        "portrait/landscape rotation, four-way scroll, and a one-call picture-in-picture "
        "expand. android_press_key, android_open_uri, and android_wait_for cover keys, "
        "deep links, and waits. Observations are compact unless detail='full'. "
        "android_reset clears the configured app data and requires confirm=true. "
        "The server uses UIAutomator state only; screenshots are not captured or sent to models."
    ),
)
_OBSERVATIONS: dict[str, Observation] = {}

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
MUTATING = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=False,
)


def _observation_payload(observation: Any) -> dict[str, Any]:
    return observation.model_dump(mode="json")


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop the node tree; keep actions and the visible text a caller reads."""

    screen_text: list[str] = []
    seen: set[str] = set()
    for node in payload["nodes"]:
        if node["password"]:
            continue
        for value in (node["text"], node["content_description"]):
            value = value.strip()
            if value and value not in seen:
                seen.add(value)
                screen_text.append(value[:SCREEN_TEXT_CHARS])
    compact = {key: value for key, value in payload.items() if key != "nodes"}
    compact["actions"] = [
        {"id": action["id"], "operation": action["operation"], "label": action["label"]}
        for action in payload["actions"]
    ]
    compact["screen_text"] = screen_text[:SCREEN_TEXT_ITEMS]
    return compact


def _shape(payload: dict[str, Any] | None, detail: Detail) -> dict[str, Any] | None:
    if payload is None or detail == "full":
        return payload
    return _compact(payload)


def _has_text(payload: dict[str, Any], needle: str) -> bool:
    return any(
        needle in f"{node['text']} {node['content_description']}".casefold()
        for node in payload["nodes"]
        if not node["password"]
    )


def _configured_packages() -> list[str]:
    packages = [entry.strip() for entry in os.environ.get(PACKAGE_ENV, "").split(",")]
    invalid = [package for package in packages if not PACKAGE_PATTERN.fullmatch(package)]
    if invalid:
        raise ValueError(
            f"{PACKAGE_ENV} must be a comma-separated list of valid Android package names; "
            f"invalid entries: {invalid}"
        )
    return packages


def _configured_package() -> str:
    return _configured_packages()[0]


def _allowed_packages() -> set[str]:
    return set(_configured_packages())


def _check_boundary(observation: Observation) -> Observation:
    if observation.package not in _allowed_packages() | SYSTEM_PACKAGES:
        raise AdbError(
            f"Foreground package {observation.package} is not in {PACKAGE_ENV} "
            f"({', '.join(_configured_packages())}). "
            f"Add it to {PACKAGE_ENV} and restart the server."
        )
    return observation


Tool = TypeVar("Tool", bound=Callable[..., Any])


def _report_failures(tool: Tool) -> Tool:
    """Return expected failures as ToolError, because MCP hides the text of any other exception."""

    @functools.wraps(tool)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return tool(*args, **kwargs)
        except (AdbError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    return wrapper  # type: ignore[return-value]


def _remember(observation: Observation) -> Observation:
    _OBSERVATIONS[observation.serial] = observation
    return observation


def _validate_package(package: str) -> str:
    if not PACKAGE_PATTERN.fullmatch(package) or package not in _allowed_packages():
        raise ValueError(
            f"Package {package} is not in {PACKAGE_ENV} ({', '.join(_configured_packages())})"
        )
    return package


def _allowed_pinned_window(client: AdbClient):
    try:
        pinned = pinned_window(client.activity_dump())
    except AdbError as exc:
        LOGGER.warning("Unable to read pinned windows: %s", exc)
        return None
    return pinned if pinned and pinned[0] in _allowed_packages() else None


def _client(serial: str | None) -> AdbClient:
    return AdbClient(AdbClient.resolve_serial(serial))


@mcp.tool(annotations=READ_ONLY)
@_report_failures
def device_list() -> list[dict[str, str]]:
    """List connected Android devices and their connection state."""

    return [device.__dict__ for device in AdbClient.list_devices()]


def _observe(serial: str | None = None) -> dict[str, Any]:
    client = _client(serial)
    capture_started = time.perf_counter()
    xml = client.ui_hierarchy_xml()
    capture_ms = round((time.perf_counter() - capture_started) * 1000)
    parse_started = time.perf_counter()
    observation = parse_observation(
        xml, client.serial or "unknown", pinned=_allowed_pinned_window(client)
    )
    parse_ms = round((time.perf_counter() - parse_started) * 1000)
    _check_boundary(observation)
    return _observation_payload(_remember(observation)) | {
        "_timings_ms": {"capture_ms": capture_ms, "parse_ms": parse_ms}
    }


@mcp.tool(annotations=READ_ONLY)
@_report_failures
def android_observe(serial: str | None = None, detail: Detail = "compact") -> dict[str, Any]:
    """Read the current UIAutomator hierarchy without taking a screenshot.

    detail="compact" returns actions and visible text; "full" adds every node.
    """

    payload = _observe(serial)
    return payload if detail == "full" else _compact(payload)


def _cached(client: AdbClient, observation_id: str) -> Observation:
    cached = _OBSERVATIONS.get(client.serial or "")
    if cached is None or cached.observation_id != observation_id:
        raise ValueError("Observation is stale or was not created by android_observe")
    return cached


def _settle(client: AdbClient, wait_seconds: float) -> dict[str, Any] | None:
    if not 0.0 <= wait_seconds <= 5.0:
        raise ValueError("wait_seconds must be between 0 and 5")
    if wait_seconds:
        time.sleep(wait_seconds)
    next_state = _next_observation(client)
    if next_state:
        next_state.pop("_timings_ms", None)
    return next_state


def _find_action(observation: Observation, action_id: str) -> Action:
    for action in observation.actions:
        if action.id == action_id:
            return action
    raise ValueError("Action is not present in the supplied observation")


def _find_node(observation: Observation, node_id: str | None):
    if node_id is None:
        return None
    for node in observation.nodes:
        if node.id == node_id:
            return node
    raise ValueError("Action node is not present in the supplied observation")


def _next_observation(client: AdbClient) -> dict[str, Any] | None:
    try:
        capture_started = time.perf_counter()
        xml = client.ui_hierarchy_xml()
        capture_ms = round((time.perf_counter() - capture_started) * 1000)
        parse_started = time.perf_counter()
        observation = parse_observation(
            xml, client.serial or "unknown", pinned=_allowed_pinned_window(client)
        )
        parse_ms = round((time.perf_counter() - parse_started) * 1000)
        _check_boundary(observation)
    except AdbError as exc:
        LOGGER.warning("Unable to capture post-action observation: %s", exc)
        return None
    return _observation_payload(_remember(observation)) | {
        "_timings_ms": {"capture_ms": capture_ms, "parse_ms": parse_ms}
    }


def _act(
    observation_id: str,
    action_id: str,
    text: str | None = None,
    scroll_direction: str = "up",
    wait_seconds: float = 0.5,
    serial: str | None = None,
) -> dict[str, Any]:
    if scroll_direction not in SCROLL_DIRECTIONS:
        raise ValueError("scroll_direction must be 'up', 'down', 'left', or 'right'")
    if not 0.0 <= wait_seconds <= 5.0:
        raise ValueError("wait_seconds must be between 0 and 5")

    client = _client(serial)
    cached = _cached(client, observation_id)
    action = _find_action(cached, action_id)
    node = _find_node(cached, action.node_id)

    action_started = time.perf_counter()
    if action.operation == "TAP":
        if node is None:
            raise ValueError("TAP action has no node")
        x = (node.bounds.left + node.bounds.right) // 2
        y = (node.bounds.top + node.bounds.bottom) // 2
        client.tap(x, y)
    elif action.operation == "LONG_PRESS":
        if node is None:
            raise ValueError("LONG_PRESS action has no node")
        x = (node.bounds.left + node.bounds.right) // 2
        y = (node.bounds.top + node.bounds.bottom) // 2
        client.long_press(x, y)
    elif action.operation == "EXPAND_PIP":
        if node is None:
            raise ValueError("EXPAND_PIP action has no node")
        x = (node.bounds.left + node.bounds.right) // 2
        y = (node.bounds.top + node.bounds.bottom) // 2
        client.tap(x, y)
        time.sleep(PIP_MENU_DELAY_SECONDS)
        if _allowed_pinned_window(client) is not None:
            client.tap(x, y)
    elif action.operation == "TYPE_TEXT":
        if text is None:
            raise ValueError("TYPE_TEXT requires text")
        client.type_text(text)
    elif action.operation == "SCROLL":
        if node is None:
            raise ValueError("SCROLL action has no node")
        x = (node.bounds.left + node.bounds.right) // 2
        y = (node.bounds.top + node.bounds.bottom) // 2
        top = node.bounds.top + max(1, (node.bounds.bottom - node.bounds.top) // 5)
        bottom = node.bounds.bottom - max(1, (node.bounds.bottom - node.bounds.top) // 5)
        left = node.bounds.left + max(1, (node.bounds.right - node.bounds.left) // 5)
        right = node.bounds.right - max(1, (node.bounds.right - node.bounds.left) // 5)
        if scroll_direction == "up":
            client.swipe(x, bottom, x, top)
        elif scroll_direction == "down":
            client.swipe(x, top, x, bottom)
        elif scroll_direction == "left":
            client.swipe(right, y, left, y)
        else:
            client.swipe(left, y, right, y)
    elif action.operation == "PRESS_ENTER":
        client.press_enter()
    elif action.operation == "BACK":
        client.back()
    elif action.operation == "HOME":
        client.home()
    elif action.operation == "ROTATE_PORTRAIT":
        client.rotate("portrait")
    elif action.operation == "ROTATE_LANDSCAPE":
        client.rotate("landscape")
    elif action.operation in {"WAIT", "DONE", "BLOCKED"}:
        pass
    else:
        raise ValueError(f"Unsupported action operation: {action.operation}")

    action_ms = round((time.perf_counter() - action_started) * 1000)
    wait_started = time.perf_counter()
    if wait_seconds:
        time.sleep(wait_seconds)
    wait_ms = round((time.perf_counter() - wait_started) * 1000)
    capture_started = time.perf_counter()
    next_state = _next_observation(client) if action.operation not in {"DONE", "BLOCKED"} else None
    observation_timings = next_state.pop("_timings_ms", {}) if next_state else {}
    return {
        "status": "completed",
        "observation_id": observation_id,
        "action_id": action_id,
        "operation": action.operation,
        "next_observation": next_state,
        "timings_ms": {
            "action_ms": action_ms,
            "wait_ms": wait_ms,
            **{f"post_action_{key}": value for key, value in observation_timings.items()},
            "post_action_capture_parse_ms": round((time.perf_counter() - capture_started) * 1000),
        },
    }


@mcp.tool(annotations=MUTATING)
@_report_failures
def android_act(
    observation_id: str,
    action_id: str,
    text: str | None = None,
    scroll_direction: str = "up",
    wait_seconds: float = 0.5,
    serial: str | None = None,
    detail: Detail = "compact",
) -> dict[str, Any]:
    """Execute one bounded action from the latest exact UIAutomator observation.

    scroll_direction names the finger movement: "up" reveals content below, "left"
    reveals content to the right.
    """

    result = _act(observation_id, action_id, text, scroll_direction, wait_seconds, serial)
    return result | {"next_observation": _shape(result["next_observation"], detail)}


@mcp.tool(
    annotations=MUTATING,
    description=(
        "Press one allowlisted key on the screen of the latest observation. Keys: "
        + ", ".join(sorted(KEYS))
        + ". Use repeat with DEL to clear a field."
    ),
)
@_report_failures
def android_press_key(
    observation_id: str,
    key: str,
    repeat: int = 1,
    wait_seconds: float = 0.5,
    serial: str | None = None,
    detail: Detail = "compact",
) -> dict[str, Any]:
    if key not in KEYS:
        raise ValueError(f"key must be one of {', '.join(sorted(KEYS))}")
    if not 1 <= repeat <= MAX_KEY_REPEAT:
        raise ValueError(f"repeat must be between 1 and {MAX_KEY_REPEAT}")
    if not 0.0 <= wait_seconds <= 5.0:
        raise ValueError("wait_seconds must be between 0 and 5")
    client = _client(serial)
    _cached(client, observation_id)
    client.press_keys(f"KEYCODE_{key}", repeat)
    return {
        "status": "completed",
        "key": key,
        "repeat": repeat,
        "next_observation": _shape(_settle(client, wait_seconds), detail),
    }


@mcp.tool(annotations=MUTATING)
@_report_failures
def android_open_uri(
    uri: str,
    package: str | None = None,
    wait_seconds: float = 1.0,
    serial: str | None = None,
    detail: Detail = "compact",
) -> dict[str, Any]:
    """Open a deep link with a VIEW intent that only a configured package can receive."""

    if len(uri) > MAX_URI_CHARS or not URI_PATTERN.fullmatch(uri):
        raise ValueError("uri must be one absolute URI such as scheme://host/path")
    package = _validate_package(package or _configured_package())
    if not 0.0 <= wait_seconds <= 5.0:
        raise ValueError("wait_seconds must be between 0 and 5")
    client = _client(serial)
    output = client.open_uri(uri, package)
    if "Error" in output:
        raise ValueError(output.strip())
    return {
        "status": "completed",
        "package": package,
        "output": output.strip(),
        "next_observation": _shape(_settle(client, wait_seconds), detail),
    }


@mcp.tool(annotations=READ_ONLY)
@_report_failures
def android_wait_for(
    text: str,
    absent: bool = False,
    timeout_seconds: float = 15.0,
    serial: str | None = None,
    detail: Detail = "compact",
) -> dict[str, Any]:
    """Poll the screen until text appears, or with absent=true until it is gone.

    Matching is a case-insensitive substring of node text or content description.
    A failed capture, common while an app starts, counts as a poll and is retried.
    The returned observation can be acted on.
    """

    needle = text.strip().casefold()
    if not needle:
        raise ValueError("text must not be empty")
    if not 0.0 < timeout_seconds <= MAX_WAIT_SECONDS:
        raise ValueError(f"timeout_seconds must be above 0 and at most {MAX_WAIT_SECONDS}")
    started = time.monotonic()
    polls = 0
    while True:
        polls += 1
        try:
            payload = _observe(serial)
        except AdbError:
            if time.monotonic() - started >= timeout_seconds:
                raise
            time.sleep(WAIT_POLL_SECONDS)
            continue
        payload.pop("_timings_ms", None)
        matched = _has_text(payload, needle) != absent
        waited = time.monotonic() - started
        if matched or waited >= timeout_seconds:
            return {
                "status": "matched" if matched else "timed_out",
                "polls": polls,
                "waited_ms": round(waited * 1000),
                "observation": _shape(payload, detail),
            }
        time.sleep(WAIT_POLL_SECONDS)


@mcp.tool(annotations=MUTATING)
@_report_failures
def android_launch(package: str | None = None, serial: str | None = None) -> dict[str, str]:
    """Launch an app's launcher activity."""

    package = _validate_package(package or _configured_package())
    output = _client(serial).launch(package)
    return {"package": package, "output": output.strip()}


@mcp.tool(annotations=DESTRUCTIVE)
@_report_failures
def android_reset(
    package: str | None = None,
    serial: str | None = None,
    confirm: bool = False,
) -> dict[str, str]:
    """Clear one app's data and launch it; use only for an explicit test reset."""

    package = _validate_package(package or _configured_package())
    if not confirm:
        raise ValueError("android_reset requires confirm=true")
    client = _client(serial)
    cleared = client.clear_app_data(package)
    launched = client.launch(package)
    return {"package": package, "clear_output": cleared.strip(), "launch_output": launched.strip()}


@mcp.tool(annotations=READ_ONLY)
@_report_failures
def android_logcat(
    lines: int = 100,
    serial: str | None = None,
    package: str | None = None,
) -> dict[str, str | int]:
    """Return a bounded recent logcat tail for diagnostics."""

    bounded_lines = max(1, min(lines, 1000))
    package = _validate_package(package or _configured_package())
    return {
        "lines": bounded_lines,
        "package": package,
        "text": _client(serial).logcat(package, bounded_lines),
    }


@mcp.tool(annotations=MUTATING)
@_report_failures
def android_run(
    goal: str,
    serial: str | None = None,
    max_steps: int = 12,
    backend: Literal["auto", "typesafe", "openrouter"] = "auto",
    text_by_resource_id: dict[str, str] | None = None,
    wait_seconds: float = 0.5,
    detail: Detail = "compact",
) -> dict[str, Any]:
    """Run a bounded screenshot-free Jev loop against the configured Android app."""

    with httpx.Client(http2=True) as http_client:
        result = run_jev(
            goal,
            _observe,
            _act,
            serial=serial,
            max_steps=max_steps,
            backend=backend,
            http_client=http_client,
            completion_oracle=completion_oracle_for_goal(goal),
            text_by_resource_id=text_by_resource_id,
            wait_seconds=wait_seconds,
        )
    if "final_observation" in result:
        result["final_observation"] = _shape(result["final_observation"], detail)
    return result


def main() -> None:
    """Run the persistent local stdio server."""

    logging.basicConfig(level=logging.INFO)
    try:
        mcp.run()
    except AdbError:
        LOGGER.exception("Android operation failed")
        raise


if __name__ == "__main__":
    main()
