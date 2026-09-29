"""Bounded Jev decision loop for the Android MCP."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from .jev import JevError, _select_backend, choose
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
    text_by_resource_id: dict[str, str] | None = None,
    wait_seconds: float = 0.5,
) -> dict[str, Any]:
    """Run a short, screenshot-free Jev loop and return a redacted trace."""

    if not goal.strip():
        raise ValueError("goal must not be empty")
    if not 1 <= max_steps <= 20:
        raise ValueError("max_steps must be between 1 and 20")
    if not 0.0 <= wait_seconds <= 5.0:
        raise ValueError("wait_seconds must be between 0 and 5")
    if text_by_resource_id is not None and any(
        not isinstance(key, str) or not key.strip() or not isinstance(value, str)
        for key, value in text_by_resource_id.items()
    ):
        raise ValueError("text_by_resource_id must map non-empty resource IDs to text strings")

    started = time.perf_counter()
    setup_timings: dict[str, int] = {}
    credential_started = time.perf_counter()
    try:
        backend_auth = _select_backend(backend)
    except JevError as exc:
        return {
            "status": "blocked",
            "reason": str(exc),
            "steps": 0,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "setup_timings_ms": {
                "credentials_ms": round((time.perf_counter() - credential_started) * 1000)
            },
            "trace": [],
        }
    setup_timings = {
        "credentials_ms": round((time.perf_counter() - credential_started) * 1000)
    }
    capture_started = time.perf_counter()
    state = dict(observe(serial=serial))
    observed_timings = state.pop("_timings_ms", {})
    setup_timings.update(
        observed_timings
        or {"capture_parse_ms": round((time.perf_counter() - capture_started) * 1000)}
    )
    history: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    rejected_done_count = 0
    for step in range(1, max_steps + 1):
        timings: dict[str, int] = {}
        parse_started = time.perf_counter()
        observation = Observation.model_validate(state)
        timings["observation_validation_ms"] = round((time.perf_counter() - parse_started) * 1000)
        try:
            model_started = time.perf_counter()
            decision = choose(
                observation,
                goal,
                history,
                backend=backend,
                http_client=http_client,
                backend_auth=backend_auth,
            )
            timings["decision_ms"] = round((time.perf_counter() - model_started) * 1000)
        except JevError as exc:
            return {
                "setup_timings_ms": dict(setup_timings),
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
            "timings_ms": dict(timings),
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
                        "setup_timings_ms": dict(setup_timings),
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
                "setup_timings_ms": dict(setup_timings),
                "status": "done" if operation == "DONE" else "blocked",
                "steps": step - 1,
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "trace": trace,
                "final_observation": state,
            }

        selected = next(action for action in observation.actions if action.id == action_id)
        text = None
        if operation == "TYPE_TEXT":
            node = next((item for item in observation.nodes if item.id == selected.node_id), None)
            supplied = (text_by_resource_id or {}).get(node.resource_id) if node else None
            if supplied is not None:
                same_id = [
                    item for item in observation.nodes
                    if item.editable and item.resource_id == node.resource_id
                ] if node else []
                if len(same_id) != 1:
                    return {
                        "setup_timings_ms": dict(setup_timings),
                        "status": "blocked",
                        "reason": "Caller text resource ID is ambiguous in the current observation",
                        "steps": step - 1,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000),
                        "trace": trace,
                        "final_observation": state,
                    }
                if not node or not node.focused:
                    return {
                        "setup_timings_ms": dict(setup_timings),
                        "status": "blocked",
                        "reason": "Caller text target is not the currently focused field",
                        "steps": step - 1,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000),
                        "trace": trace,
                        "final_observation": state,
                    }
                if not isinstance(supplied, str) or not supplied.strip() or len(supplied) > 2000:
                    reason = "Caller text must be a non-empty string of at most 2000 characters"
                    trace.append({**trace_item, "status": "blocked", "reason": reason})
                    return {
                        "setup_timings_ms": dict(setup_timings),
                        "status": "blocked", "reason": reason, "steps": step - 1,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000),
                        "trace": trace, "final_observation": state,
                    }
                text = supplied
                trace_item["text_source"] = "caller"
            else:
                text_started = time.perf_counter()
                try:
                    text, helper = field_text(
                        goal, observation, selected, history, http_client=http_client
                    )
                except JevError as exc:
                    trace.append({**trace_item, "status": "blocked", "reason": str(exc)})
                    return {
                        "setup_timings_ms": dict(setup_timings),
                        "status": "blocked", "reason": str(exc), "steps": step - 1,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000),
                        "trace": trace, "final_observation": state,
                    }
                timings["text_helper_ms"] = round((time.perf_counter() - text_started) * 1000)
                trace_item["timings_ms"] = dict(timings)
                trace_item["text_source"] = "model"
                trace_item["text_helper"] = helper["model"]

        action_started = time.perf_counter()
        try:
            result = act(
                observation_id=observation.observation_id,
                action_id=action_id,
                text=text,
                serial=serial,
                wait_seconds=wait_seconds,
            )
        except Exception as exc:
            trace.append({**trace_item, "status": "blocked", "reason": str(exc)})
            return {
                "setup_timings_ms": dict(setup_timings),
                "status": "blocked",
                "reason": "Android action failed",
                "steps": step - 1,
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "trace": trace,
                "final_observation": state,
            }
        timings["action_call_ms"] = round((time.perf_counter() - action_started) * 1000)
        timings.update(result.get("timings_ms", {}))
        trace_item["timings_ms"] = dict(timings)

        next_state = result.get("next_observation")
        if not next_state:
            try:
                capture_started = time.perf_counter()
                next_state = dict(observe(serial=serial))
                observed_timings = next_state.pop("_timings_ms", {})
                timings.update(
                    observed_timings
                    or {"capture_parse_ms": round((time.perf_counter() - capture_started) * 1000)}
                )
            except Exception:
                next_state = state
        trace_item["timings_ms"] = dict(timings)
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
                "setup_timings_ms": dict(setup_timings),
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
                "setup_timings_ms": dict(setup_timings),
                "status": "blocked",
                "reason": "Three non-wait actions made no observable progress",
                "steps": step,
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "trace": trace,
                "final_observation": state,
            }

    return {
        "setup_timings_ms": dict(setup_timings),
        "status": "blocked",
        "reason": "Reached the bounded Jev action budget",
        "steps": max_steps,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "trace": trace,
        "final_observation": state,
    }
