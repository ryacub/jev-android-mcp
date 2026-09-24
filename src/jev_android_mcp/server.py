"""Local stdio MCP server for bounded Android emulator operations."""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Literal

import httpx
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from jev_android_mcp.adb import AdbClient, AdbError
from jev_android_mcp.agent import completion_oracle_for_goal, run_jev
from jev_android_mcp.models import Action, Observation
from jev_android_mcp.state import parse_observation

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
PACKAGE_PATTERN = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$")

mcp = MCPServer(
    "Jev Android MCP",
    instructions=(
        "Use android_observe before android_act. Actions are bound to one "
        "observation ID. android_act supports Home and fixed portrait/landscape "
        "rotation. android_reset clears the configured app data and requires "
        "confirm=true. The server uses UIAutomator state only; screenshots "
        "are not captured or sent to models."
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


def _configured_package() -> str:
    package = os.environ.get(PACKAGE_ENV, "").strip()
    if not PACKAGE_PATTERN.fullmatch(package):
        raise ValueError(f"{PACKAGE_ENV} must be a valid Android package name")
    return package


def _allowed_packages() -> set[str]:
    return {_configured_package()}


def _remember(observation: Observation) -> Observation:
    _OBSERVATIONS[observation.serial] = observation
    return observation


def _validate_package(package: str) -> str:
    if not PACKAGE_PATTERN.fullmatch(package) or package not in _allowed_packages():
        raise ValueError(f"Package is outside the configured allowlist: {package}")
    return package


def _client(serial: str | None) -> AdbClient:
    return AdbClient(AdbClient.resolve_serial(serial))


@mcp.tool(annotations=READ_ONLY)
def device_list() -> list[dict[str, str]]:
    """List connected Android devices and their connection state."""

    return [device.__dict__ for device in AdbClient.list_devices()]


@mcp.tool(annotations=READ_ONLY)
def android_observe(serial: str | None = None) -> dict[str, Any]:
    """Read the current UIAutomator hierarchy without taking a screenshot."""

    client = _client(serial)
    xml = client.ui_hierarchy_xml()
    observation = parse_observation(xml, client.serial or "unknown")
    if observation.package not in _allowed_packages() | SYSTEM_PACKAGES:
        raise AdbError(
            f"Observed package is outside the configured boundary: {observation.package}"
        )
    return _observation_payload(_remember(observation))


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
        xml = client.ui_hierarchy_xml()
        observation = parse_observation(xml, client.serial or "unknown")
        if observation.package not in _allowed_packages() | SYSTEM_PACKAGES:
            raise AdbError(
                f"Observed package is outside the configured boundary: {observation.package}"
            )
    except AdbError as exc:
        LOGGER.warning("Unable to capture post-action observation: %s", exc)
        return None
    return _observation_payload(_remember(observation))


@mcp.tool(annotations=MUTATING)
def android_act(
    observation_id: str,
    action_id: str,
    text: str | None = None,
    scroll_direction: str = "up",
    wait_seconds: float = 0.5,
    serial: str | None = None,
) -> dict[str, Any]:
    """Execute one bounded action from the latest exact UIAutomator observation."""

    if scroll_direction not in {"up", "down"}:
        raise ValueError("scroll_direction must be 'up' or 'down'")
    if not 0.0 <= wait_seconds <= 5.0:
        raise ValueError("wait_seconds must be between 0 and 5")

    client = _client(serial)
    cached = _OBSERVATIONS.get(client.serial or "")
    if cached is None or cached.observation_id != observation_id:
        raise ValueError("Observation is stale or was not created by android_observe")
    action = _find_action(cached, action_id)
    node = _find_node(cached, action.node_id)

    if action.operation == "TAP":
        if node is None:
            raise ValueError("TAP action has no node")
        x = (node.bounds.left + node.bounds.right) // 2
        y = (node.bounds.top + node.bounds.bottom) // 2
        client.tap(x, y)
    elif action.operation == "TYPE_TEXT":
        if text is None:
            raise ValueError("TYPE_TEXT requires text")
        client.type_text(text)
    elif action.operation == "SCROLL":
        if node is None:
            raise ValueError("SCROLL action has no node")
        x = (node.bounds.left + node.bounds.right) // 2
        top = node.bounds.top + max(1, (node.bounds.bottom - node.bounds.top) // 5)
        bottom = node.bounds.bottom - max(1, (node.bounds.bottom - node.bounds.top) // 5)
        if scroll_direction == "up":
            client.swipe(x, bottom, x, top)
        else:
            client.swipe(x, top, x, bottom)
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

    if wait_seconds:
        time.sleep(wait_seconds)
    next_state = _next_observation(client) if action.operation not in {"DONE", "BLOCKED"} else None
    return {
        "status": "completed",
        "observation_id": observation_id,
        "action_id": action_id,
        "operation": action.operation,
        "next_observation": next_state,
    }


@mcp.tool(annotations=MUTATING)
def android_launch(package: str | None = None, serial: str | None = None) -> dict[str, str]:
    """Launch an app's launcher activity."""

    package = _validate_package(package or _configured_package())
    output = _client(serial).launch(package)
    return {"package": package, "output": output.strip()}


@mcp.tool(annotations=DESTRUCTIVE)
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
def android_logcat(lines: int = 100, serial: str | None = None) -> dict[str, str | int]:
    """Return a bounded recent logcat tail for diagnostics."""

    bounded_lines = max(1, min(lines, 1000))
    package = _configured_package()
    return {
        "lines": bounded_lines,
        "package": package,
        "text": _client(serial).logcat(package, bounded_lines),
    }


@mcp.tool(annotations=MUTATING)
def android_run(
    goal: str,
    serial: str | None = None,
    max_steps: int = 12,
    backend: Literal["auto", "typesafe", "openrouter"] = "auto",
) -> dict[str, Any]:
    """Run a bounded screenshot-free Jev loop against the configured Android app."""

    with httpx.Client(http2=True) as http_client:
        return run_jev(
            goal,
            android_observe,
            android_act,
            serial=serial,
            max_steps=max_steps,
            backend=backend,
            http_client=http_client,
            completion_oracle=completion_oracle_for_goal(goal),
        )


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
