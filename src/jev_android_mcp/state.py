"""UIAutomator parsing and action-space construction."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from typing import Any

from .models import Action, Bounds, Observation, UiNode


def _boolean(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() == "true"


def _bounds(value: str | None) -> Bounds:
    if not value or not (value.startswith("[") and "][" in value and value.endswith("]")):
        return Bounds(left=0, top=0, right=0, bottom=0)
    try:
        left_top, right_bottom = value[1:-1].split("][")
        left, top = (int(part) for part in left_top.split(","))
        right, bottom = (int(part) for part in right_bottom.split(","))
    except (ValueError, TypeError):
        return Bounds(left=0, top=0, right=0, bottom=0)
    return Bounds(left=left, top=top, right=right, bottom=bottom)


def _walk(element: ET.Element, path: str = "0") -> Iterator[tuple[ET.Element, str]]:
    yield element, path
    for index, child in enumerate(element, start=0):
        yield from _walk(child, f"{path}/{index}")


def _node(element: ET.Element, index: int, path: str) -> UiNode:
    attrs = element.attrib
    password = _boolean(attrs.get("password"))
    return UiNode(
        id=f"node-{index}",
        path=path,
        text="" if password else attrs.get("text", ""),
        content_description="" if password else attrs.get("content-desc", ""),
        resource_id=attrs.get("resource-id", ""),
        class_name=attrs.get("class", ""),
        package=attrs.get("package", ""),
        bounds=_bounds(attrs.get("bounds")),
        enabled=_boolean(attrs.get("enabled"), True),
        clickable=_boolean(attrs.get("clickable")),
        long_clickable=_boolean(attrs.get("long-clickable")),
        focusable=_boolean(attrs.get("focusable")),
        focused=_boolean(attrs.get("focused")),
        scrollable=_boolean(attrs.get("scrollable")),
        checkable=_boolean(attrs.get("checkable")),
        checked=_boolean(attrs.get("checked")),
        selected=_boolean(attrs.get("selected")),
        password=password,
    )


def _node_label(node: UiNode) -> str:
    label = node.label.strip()
    return label if len(label) <= 160 else f"{label[:157]}..."


def _action_label(node: UiNode, descendant_labels: dict[str, list[str]]) -> str:
    """Prefer semantic text nested inside Compose-style clickable containers."""

    direct = node.content_description.strip() or node.text.strip()
    labels = descendant_labels.get(node.path, [])
    if direct:
        labels = [direct, *[label for label in labels if label != direct]]
    elif not labels:
        labels = [node.label.strip()]
    return _node_label(node.model_copy(update={"text": " · ".join(labels)}))


VIDEO_SURFACE_CLASSES = {"android.view.SurfaceView", "android.view.TextureView"}


def _is_video_surface(node: UiNode) -> bool:
    return node.class_name in VIDEO_SURFACE_CLASSES


def _video_surface_label(node: UiNode) -> str:
    resource_name = node.resource_id.rpartition("/")[2]
    return f"Video surface ({resource_name})" if resource_name else "Video surface"


RECENTS_LAUNCHERS = {"com.android.launcher3", "com.google.android.apps.nexuslauncher"}


def recents_card_labels(nodes: list[UiNode]) -> dict[str, str]:
    """Identify visible task cards from the launcher's labeled snapshot nodes."""
    by_path = {node.path: node for node in nodes}
    cards = {}
    for snapshot in nodes:
        if (
            snapshot.package not in RECENTS_LAUNCHERS
            or snapshot.resource_id != f"{snapshot.package}:id/snapshot"
            or not snapshot.content_description
            or not snapshot.has_area
        ):
            continue
        card = by_path.get(snapshot.path.rpartition("/")[0])
        if (
            card
            and card.package == snapshot.package
            and card.resource_id == f"{snapshot.package}:id/task_view_single"
            and card.clickable
        ):
            cards[card.id] = snapshot.content_description
    return cards


PINNED_PATH = "pip"
PINNED_TASK = re.compile(r"\* Task\{.*\bA=\d+:(?P<package>[\w.]+) .*\bmode=pinned\b")
PINNED_BOUNDS = re.compile(
    r"mBounds=Rect\((\d+), (\d+) - (\d+), (\d+)\).*\bmWindowingMode=pinned\b"
)


def pinned_window(activity_dump: str) -> tuple[str, Bounds] | None:
    """Return the package and bounds of the pinned task in a dumpsys activity dump.

    UIAutomator dumps only the active window, so a picture-in-picture window never
    appears in the hierarchy.
    """

    package = None
    for line in activity_dump.splitlines():
        if package is None:
            if task := PINNED_TASK.search(line):
                package = task["package"]
        elif bounds := PINNED_BOUNDS.search(line):
            left, top, right, bottom = (int(value) for value in bounds.groups())
            return package, Bounds(left=left, top=top, right=right, bottom=bottom)
    return None


def _pinned_node(index: int, package: str, bounds: Bounds) -> UiNode:
    return UiNode(
        id=f"node-{index}",
        path=PINNED_PATH,
        content_description=f"Picture-in-picture window ({package})",
        class_name="PictureInPictureWindow",
        package=package,
        bounds=bounds,
        clickable=True,
    )


def _actions(nodes: list[UiNode], observation_id: str) -> list[Action]:
    actions: list[Action] = []
    descendant_labels: dict[str, list[str]] = {}
    descendant_label_sets: dict[str, set[str]] = {}
    actionable_paths = {
        node.path
        for node in nodes
        if node.enabled
        and node.has_area
        and (node.clickable or node.long_clickable or node.editable or node.scrollable)
    }
    for candidate in nodes:
        label = candidate.content_description.strip() or candidate.text.strip()
        if not label:
            continue
        path = candidate.path.rpartition("/")[0]
        while path:
            if path in actionable_paths:
                labels = descendant_label_sets.setdefault(path, set())
                if label not in labels:
                    labels.add(label)
                    descendant_labels.setdefault(path, []).append(label)
            path = path.rpartition("/")[0]
    task_cards = recents_card_labels(nodes)
    for node in nodes:
        label = _action_label(node, descendant_labels)
        if not label or not node.enabled or not node.has_area:
            continue
        if node.clickable:
            actions.append(
                Action(
                    id=f"{observation_id}:tap:{node.id}",
                    observation_id=observation_id,
                    operation="TAP",
                    node_id=node.id,
                    label=label,
                )
            )
        elif _is_video_surface(node):
            actions.append(
                Action(
                    id=f"{observation_id}:tap:{node.id}",
                    observation_id=observation_id,
                    operation="TAP",
                    node_id=node.id,
                    label=_video_surface_label(node),
                )
            )
        if node.long_clickable:
            actions.append(
                Action(
                    id=f"{observation_id}:long_press:{node.id}",
                    observation_id=observation_id,
                    operation="LONG_PRESS",
                    node_id=node.id,
                    label=label,
                )
            )
        if node.id in task_cards:
            actions.append(
                Action(
                    id=f"{observation_id}:swipe_away_task:{node.id}",
                    observation_id=observation_id,
                    operation="SWIPE_AWAY_TASK",
                    node_id=node.id,
                    label=f"Swipe away {task_cards[node.id]}",
                )
            )
        if node.path == PINNED_PATH:
            actions.append(
                Action(
                    id=f"{observation_id}:expand_pip:{node.id}",
                    observation_id=observation_id,
                    operation="EXPAND_PIP",
                    node_id=node.id,
                    label="Expand the picture-in-picture window to full screen",
                )
            )
            actions.append(
                Action(
                    id=f"{observation_id}:dismiss_pip:{node.id}",
                    observation_id=observation_id,
                    operation="DISMISS_PIP",
                    node_id=node.id,
                    label="Close the picture-in-picture window",
                )
            )
        if node.editable:
            actions.append(
                Action(
                    id=f"{observation_id}:type:{node.id}",
                    observation_id=observation_id,
                    operation="TYPE_TEXT",
                    node_id=node.id,
                    label=label,
                )
            )
        if node.scrollable:
            actions.append(
                Action(
                    id=f"{observation_id}:scroll:{node.id}",
                    observation_id=observation_id,
                    operation="SCROLL",
                    node_id=node.id,
                    label=label,
                )
            )
    actions.extend(
        [
            Action(
                id=f"{observation_id}:PRESS_ENTER",
                observation_id=observation_id,
                operation="PRESS_ENTER",
                label="Press Enter on the focused control",
            ),
            Action(
                id=f"{observation_id}:BACK",
                observation_id=observation_id,
                operation="BACK",
                label="Android back",
            ),
            Action(
                id=f"{observation_id}:HOME",
                observation_id=observation_id,
                operation="HOME",
                label="Android Home",
            ),
            Action(
                id=f"{observation_id}:RECENTS",
                observation_id=observation_id,
                operation="RECENTS",
                label="Open Android Recents",
            ),
            Action(
                id=f"{observation_id}:ROTATE_PORTRAIT",
                observation_id=observation_id,
                operation="ROTATE_PORTRAIT",
                label="Rotate to portrait",
            ),
            Action(
                id=f"{observation_id}:ROTATE_LANDSCAPE",
                observation_id=observation_id,
                operation="ROTATE_LANDSCAPE",
                label="Rotate to landscape",
            ),
            Action(
                id=f"{observation_id}:WAIT",
                observation_id=observation_id,
                operation="WAIT",
                label="Wait for a meaningful UI change",
            ),
            Action(
                id=f"{observation_id}:DONE",
                observation_id=observation_id,
                operation="DONE",
                label="The requested result is visibly satisfied",
            ),
            Action(
                id=f"{observation_id}:BLOCKED",
                observation_id=observation_id,
                operation="BLOCKED",
                label="No supported action can make progress",
            ),
        ],
    )
    return actions


def _fingerprint(
    serial: str,
    package: str,
    activity: str | None,
    rotation: int | None,
    nodes: list[UiNode],
) -> str:
    payload: list[dict[str, Any]] = [
        {
            "serial": serial,
            "package": package,
            "activity": activity,
            "rotation": rotation,
            "path": node.path,
            "text": node.text,
            "content_description": node.content_description,
            "resource_id": node.resource_id,
            "class_name": node.class_name,
            "bounds": node.bounds.model_dump(),
            "enabled": node.enabled,
            "clickable": node.clickable,
            "focusable": node.focusable,
            "focused": node.focused,
            "scrollable": node.scrollable,
            "checked": node.checked,
            "selected": node.selected,
        }
        for node in nodes
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:20]


def parse_observation(
    xml: str,
    serial: str,
    activity: str | None = None,
    pinned: tuple[str, Bounds] | None = None,
) -> Observation:
    """Parse a UIAutomator XML dump into a compact, indexed observation."""

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError("UIAutomator returned invalid XML") from exc

    observation_id = uuid.uuid4().hex
    nodes = [_node(element, index, path) for index, (element, path) in enumerate(_walk(root))]
    package = root.attrib.get("package", "")
    if not package:
        package = next((node.package for node in nodes if node.package), "")
    rotation_text = root.attrib.get("rotation")
    rotation = int(rotation_text) if rotation_text and rotation_text.isdigit() else None
    if pinned:
        nodes.append(_pinned_node(len(nodes), *pinned))
    return Observation(
        serial=serial,
        observation_id=observation_id,
        package=package,
        activity=activity,
        rotation=rotation,
        fingerprint=_fingerprint(serial, package, activity, rotation, nodes),
        nodes=nodes,
        actions=_actions(nodes, observation_id),
    )
