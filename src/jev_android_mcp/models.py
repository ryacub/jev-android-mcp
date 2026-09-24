"""Typed models for compact Android observations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Bounds(BaseModel):
    """Pixel bounds reported by UIAutomator."""

    model_config = ConfigDict(frozen=True)

    left: int
    top: int
    right: int
    bottom: int


class UiNode(BaseModel):
    """A visible UI node with enough information for a bounded action."""

    model_config = ConfigDict(frozen=True)

    id: str
    path: str
    text: str = ""
    content_description: str = ""
    resource_id: str = ""
    class_name: str = ""
    package: str = ""
    bounds: Bounds
    enabled: bool = True
    clickable: bool = False
    focusable: bool = False
    focused: bool = False
    scrollable: bool = False
    checkable: bool = False
    checked: bool = False
    selected: bool = False
    password: bool = False

    @property
    def has_area(self) -> bool:
        """Return whether the node has a usable on-screen area."""

        return self.bounds.right > self.bounds.left and self.bounds.bottom > self.bounds.top

    @property
    def label(self) -> str:
        """Return the best non-sensitive human-readable label."""

        if self.password:
            return self.content_description or self.resource_id or self.class_name
        return self.content_description or self.text or self.resource_id or self.class_name

    @property
    def editable(self) -> bool:
        """Return whether this node looks like a text input."""

        return "edittext" in self.class_name.lower() or "textfield" in self.class_name.lower()


class Action(BaseModel):
    """A bounded operation against one currently observed node."""

    model_config = ConfigDict(frozen=True)

    id: str
    observation_id: str
    operation: str
    node_id: str | None = None
    label: str


class ActionRequest(BaseModel):
    """A request to execute one action from one exact observation."""

    observation_id: str
    action_id: str
    text: str | None = None
    scroll_direction: Literal["up", "down"] = "up"
    wait_seconds: float = Field(default=0.5, ge=0.0, le=5.0)


class Observation(BaseModel):
    """Compact state sent to an agent or used by the executor."""

    model_config = ConfigDict(frozen=True)

    serial: str
    observation_id: str
    package: str
    activity: str | None = None
    rotation: int | None = None
    fingerprint: str
    nodes: list[UiNode] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
