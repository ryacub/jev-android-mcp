"""Screenshot-free completion oracles for structured Android state."""

from __future__ import annotations

from .models import Observation


def selected_tab_completion(
    observation: Observation,
    goal: str,
    *,
    package: str,
    default_tab: str | None = None,
    tabs: tuple[str, ...] = (),
) -> bool:
    """Verify that a goal's requested tab is selected by UIAutomator semantics."""

    if observation.package != package:
        return False

    normalized_goal = goal.casefold()
    mentioned_tabs = [tab for tab in tabs if tab.casefold() in normalized_goal]
    if len(mentioned_tabs) > 1:
        return False
    requested_tab = mentioned_tabs[0] if mentioned_tabs else default_tab
    if requested_tab is None:
        return False
    requested_tab = requested_tab.casefold()

    selected_nodes = (node for node in observation.nodes if node.selected)
    for selected in selected_nodes:
        prefix = f"{selected.path}/"
        for node in observation.nodes:
            if not node.path.startswith(prefix):
                continue
            if node.label.strip().casefold() == requested_tab:
                return True
    return False
