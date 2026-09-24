"""Bounded Jev decision loop for the Android MCP."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from .jev import JevError, choose
from .models import Observation
from .openrouter import field_text

Observe = Callable[..., dict[str, Any]]
Act = Callable[..., dict[str, Any]]
CompletionOracle = Callable[[Observation, str], bool]
LAUNCHER_PACKAGES = {
    "com.google.android.apps.nexuslauncher",
    "com.android.launcher3",
}


def android_completion(observation: Observation, goal: str) -> bool:
    """Verify goals that are independent of the app under test."""

    normalized_goal = goal.casefold()
    if "home" in normalized_goal or "launcher" in normalized_goal:
        return observation.package in LAUNCHER_PACKAGES
    if "landscape" in normalized_goal:
        return observation.rotation == 1
    if "portrait" in normalized_goal:
        return observation.rotation == 0
    return False


def completion_oracle_for_goal(goal: str) -> CompletionOracle | None:
    """Return built-in verification only for emulator-level goals."""

    normalized_goal = goal.casefold()
    emulator_goal_terms = ("home", "launcher", "landscape", "portrait")
    if any(term in normalized_goal for term in emulator_goal_terms):
        return android_completion
    return None


def run_jev(
    goal: str,
    observe: Observe,
    act: Act,
    *,
    serial: str | None = None,
    max_steps: int = 12,
    backend: str | None = None,
    http_client: httpx.Client | None = None,
    completion_oracle: CompletionOracle | None = None,
) -> dict[str, Any]:
    """Run a short, screenshot-free Jev loop and return a redacted trace."""

    if not goal.strip():
        raise ValueError("goal must not be empty")
    if not 1 <= max_steps <= 20:
        raise ValueError("max_steps must be between 1 and 20")

    started = time.perf_counter()
    state = observe(serial=serial)
    history: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    rejected_done_count = 0
    for step in range(1, max_steps + 1):
        observation = Observation.model_validate(state)
        try:
            decision = choose(
                observation,
                goal,
                history,
                backend=backend,
                http_client=http_client,
            )
        except JevError as exc:
            return {
                "status": "blocked",
                "reason": str(exc),
                "steps": step - 1,
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "trace": trace,
            }

        action_id = decision["action_id"]
        operation = decision["operation"]
        trace_item = {
            "step": step,
            "action_id": action_id,
            "operation": operation,
            "decision_latency_ms": decision["latency_ms"],
            "confidence": decision["confidence"],
        }
        if operation in {"DONE", "BLOCKED"}:
            if (
                operation == "DONE"
                and completion_oracle is not None
                and not completion_oracle(observation, goal)
            ):
                trace.append(
                    {
                        **trace_item,
                        "status": "done_rejected",
                        "reason": "Completion oracle did not verify the goal",
                    }
                )
                history.append(
                    {
                        "action_id": action_id,
                        "operation": operation,
                        "page_changed": False,
                        "completion_verified": False,
                        "result": "completion_rejected",
                    }
                )
                rejected_done_count += 1
                if rejected_done_count >= 2:
                    return {
                        "status": "blocked",
                        "reason": "Jev repeated DONE without independent completion evidence",
                        "steps": step - 1,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000),
                        "trace": trace,
                        "final_observation": state,
                    }
                continue
            trace.append({**trace_item, "status": operation.lower()})
            return {
                "status": "done" if operation == "DONE" else "blocked",
                "steps": step - 1,
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "trace": trace,
                "final_observation": state,
            }

        selected = next(action for action in observation.actions if action.id == action_id)
        text = None
        if operation == "TYPE_TEXT":
            try:
                text, helper = field_text(goal, observation, selected, history)
            except JevError as exc:
                trace.append({**trace_item, "status": "blocked", "reason": str(exc)})
                return {
                    "status": "blocked",
                    "reason": str(exc),
                    "steps": step - 1,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                    "trace": trace,
                    "final_observation": state,
                }
            trace_item["text_helper"] = helper["model"]

        try:
            result = act(
                observation_id=observation.observation_id,
                action_id=action_id,
                text=text,
                serial=serial,
                wait_seconds=0.5,
            )
        except Exception as exc:
            trace.append({**trace_item, "status": "blocked", "reason": str(exc)})
            return {
                "status": "blocked",
                "reason": "Android action failed",
                "steps": step - 1,
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "trace": trace,
                "final_observation": state,
            }

        next_state = result.get("next_observation")
        if not next_state:
            try:
                next_state = observe(serial=serial)
            except Exception:
                next_state = state
        page_changed = next_state.get("fingerprint") != state.get("fingerprint")
        next_observation = Observation.model_validate(next_state)
        completion_verified = bool(
            completion_oracle is not None and completion_oracle(next_observation, goal)
        )
        trace.append(
            {
                **trace_item,
                "status": "completed",
                "page_changed": page_changed,
                "completion_verified": completion_verified,
            }
        )
        if completion_verified:
            return {
                "status": "done",
                "steps": step,
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "trace": trace,
                "final_observation": next_state,
            }
        history.append(
            {"action_id": action_id, "operation": operation, "page_changed": page_changed}
        )
        state = next_state
        repeated = history[-3:]
        if len(repeated) == 3 and all(
            not item["page_changed"] and item["operation"] != "WAIT" for item in repeated
        ):
            return {
                "status": "blocked",
                "reason": "Three non-wait actions made no observable progress",
                "steps": step,
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "trace": trace,
                "final_observation": state,
            }

    return {
        "status": "blocked",
        "reason": "Reached the bounded Jev action budget",
        "steps": max_steps,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "trace": trace,
        "final_observation": state,
    }
