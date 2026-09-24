"""UIAutomator parsing and action-space construction."""

from __future__ import annotations

import hashlib
import json
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


def _action_label(node: UiNode, nodes: list[UiNode]) -> str:
    """Prefer semantic text nested inside Compose-style clickable containers."""

    direct = node.content_description.strip() or node.text.strip()
    descendant_labels: list[str] = []
    prefix = f"{node.path}/"
    for candidate in nodes:
        if not candidate.path.startswith(prefix):
            continue
        label = candidate.content_description.strip() or candidate.text.strip()
        if label and label not in descendant_labels:
            descendant_labels.append(label)
    if direct:
        labels = [direct, *[label for label in descendant_labels if label != direct]]
    elif descendant_labels:
        labels = descendant_labels
    else:
        labels = [node.label.strip()]
    return _node_label(node.model_copy(update={"text": " · ".join(labels)}))


def _actions(nodes: list[UiNode], observation_id: str) -> list[Action]:
    actions: list[Action] = []
    for node in nodes:
        label = _action_label(node, nodes)
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


def parse_observation(xml: str, serial: str, activity: str | None = None) -> Observation:
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
