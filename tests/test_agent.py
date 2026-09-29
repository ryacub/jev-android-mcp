from typing import Any

import pytest
from test_state import XML

from jev_android_mcp import agent
from jev_android_mcp.completion import selected_tab_completion
from jev_android_mcp.state import parse_observation


@pytest.fixture(autouse=True)
def _local_provider_credentials(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(agent, "_select_backend", lambda backend=None: ("openrouter", "test-key"))


def _state(xml: str, package: str = "com.example.reader") -> dict[str, Any]:
    return parse_observation(xml, "emulator-5554").model_dump() | {"package": package}


def test_android_completion_verifies_home_launcher() -> None:
    xml = XML.replace(
        'package="com.example.reader"',
        'package="com.google.android.apps.nexuslauncher"',
    )
    observation = parse_observation(xml, "emulator-5554")

    assert agent.android_completion(observation, "Press the Android Home button")


def test_android_completion_verifies_rotation() -> None:
    landscape_xml = XML.replace('rotation="0"', 'rotation="1"')
    observation = parse_observation(landscape_xml, "emulator-5554")

    assert agent.android_completion(observation, "Rotate the screen to landscape")


def test_completion_oracle_is_only_built_in_for_emulator_goals() -> None:
    assert agent.completion_oracle_for_goal("Open the main screen") is None
    assert agent.completion_oracle_for_goal("Press Home") is agent.android_completion
    assert agent.completion_oracle_for_goal("Rotate to portrait") is agent.android_completion


def test_selected_tab_completion_is_reusable_for_another_package() -> None:
    xml = """<hierarchy rotation="0">
      <node text="" resource-id="" class="android.widget.FrameLayout"
        package="com.example.reader" clickable="false" enabled="true"
        focusable="false" focused="false" scrollable="false" checkable="false"
        checked="false" selected="false" password="false" bounds="[0,0][1080,2400]">
        <node text="" resource-id="" class="android.view.View"
          package="com.example.reader" clickable="true" enabled="true"
          focusable="true" focused="false" scrollable="false" checkable="false"
          checked="false" selected="true" password="false" bounds="[0,2000][216,2300]">
          <node text="Books" resource-id="" class="android.widget.TextView"
            package="com.example.reader" clickable="false" enabled="true"
            focusable="false" focused="false" scrollable="false" checkable="false"
            checked="false" selected="false" password="false" bounds="[0,2100][216,2200]" />
        </node>
      </node>
    </hierarchy>"""
    observation = parse_observation(xml, "emulator-5554")

    assert selected_tab_completion(
        observation,
        "Open the library",
        package="com.example.reader",
        default_tab="books",
    )


def test_selected_tab_completion_accepts_requested_tab() -> None:
    xml = """<hierarchy rotation="0">
      <node text="" resource-id="" class="android.widget.FrameLayout"
        package="com.example.reader" clickable="false" enabled="true"
        focusable="false" focused="false" scrollable="false" checkable="false"
        checked="false" selected="false" password="false" bounds="[0,0][1080,2400]">
      <node text="" resource-id="" class="android.view.View"
          package="com.example.reader" clickable="true" enabled="true"
          focusable="true" focused="false" scrollable="false" checkable="false"
          checked="false" selected="true" password="false" bounds="[0,2000][216,2300]">
          <node text="Books" resource-id="" class="android.widget.TextView"
            package="com.example.reader" clickable="false" enabled="true"
            focusable="false" focused="false" scrollable="false" checkable="false"
            checked="false" selected="false" password="false" bounds="[0,2100][216,2200]" />
        </node>
        <node text="Authors" resource-id="" class="android.widget.TextView"
          package="com.example.reader" clickable="false" enabled="true"
          focusable="false" focused="false" scrollable="false" checkable="false"
          checked="false" selected="false" password="false" bounds="[216,2100][432,2200]" />
      </node>
    </hierarchy>"""
    observation = parse_observation(xml, "emulator-5554")

    assert selected_tab_completion(
        observation,
        "Open the Books section",
        package="com.example.reader",
        tabs=("books", "authors"),
    )


def test_selected_tab_completion_rejects_wrong_requested_tab() -> None:
    xml = """<hierarchy rotation="0">
      <node text="" resource-id="" class="android.widget.FrameLayout"
        package="com.example.reader" clickable="false" enabled="true"
        focusable="false" focused="false" scrollable="false" checkable="false"
        checked="false" selected="false" password="false" bounds="[0,0][1080,2400]">
      <node text="" resource-id="" class="android.view.View"
          package="com.example.reader" clickable="true" enabled="true"
          focusable="true" focused="false" scrollable="false" checkable="false"
          checked="false" selected="true" password="false" bounds="[216,2000][432,2300]">
          <node text="Authors" resource-id="" class="android.widget.TextView"
            package="com.example.reader" clickable="false" enabled="true"
            focusable="false" focused="false" scrollable="false" checkable="false"
            checked="false" selected="false" password="false" bounds="[216,2100][432,2200]" />
        </node>
        <node text="Books" resource-id="" class="android.widget.TextView"
          package="com.example.reader" clickable="false" enabled="true"
          focusable="false" focused="false" scrollable="false" checkable="false"
          checked="false" selected="false" password="false" bounds="[0,2100][216,2200]" />
      </node>
    </hierarchy>"""
    observation = parse_observation(xml, "emulator-5554")

    assert not selected_tab_completion(
        observation,
        "Open the Books section",
        package="com.example.reader",
        tabs=("books", "authors"),
    )
    assert selected_tab_completion(
        observation,
        "Open the Authors section",
        package="com.example.reader",
        tabs=("books", "authors"),
    )


def test_run_jev_returns_done_after_oracle_verifies_action(monkeypatch) -> None:
    first = _state(XML)
    selected_false = (
        'focused="false" scrollable="true"\n'
        '          checkable="false" checked="false" selected="false"'
    )
    selected_true = selected_false.replace('selected="false"', 'selected="true"')
    second_xml = XML.replace(
        selected_false,
        selected_true,
    )
    second = _state(second_xml)
    tap_id = next(action["id"] for action in first["actions"] if action["operation"] == "TAP")

    monkeypatch.setattr(
        agent,
        "choose",
        lambda *args, **kwargs: {
            "action_id": tap_id,
            "operation": "TAP",
            "latency_ms": 1,
            "confidence": 1.0,
        },
    )

    result = agent.run_jev(
        "Open the main library",
        lambda **kwargs: first,
        lambda **kwargs: {"next_observation": second},
        completion_oracle=lambda observation, goal: (
            observation.fingerprint == second["fingerprint"]
        ),
    )

    assert result["status"] == "done"
    assert result["steps"] == 1
    assert result["trace"][0]["completion_verified"] is True


def test_run_jev_rejects_unverified_done(monkeypatch) -> None:
    state = _state(XML)
    monkeypatch.setattr(
        agent,
        "choose",
        lambda *args, **kwargs: {
            "action_id": next(a["id"] for a in state["actions"] if a["operation"] == "DONE"),
            "operation": "DONE",
            "latency_ms": 1,
            "confidence": 1.0,
        },
    )

    result = agent.run_jev(
        "Open the main library",
        lambda **kwargs: state,
        lambda **kwargs: {},
        completion_oracle=lambda observation, goal: False,
    )

    assert result["status"] == "blocked"
    assert "repeated DONE" in result["reason"]
    assert [item["status"] for item in result["trace"]] == ["done_rejected", "done_rejected"]


def test_run_jev_replans_after_rejected_done(monkeypatch) -> None:
    first_xml = XML
    second_xml = XML.replace('text="Search"', 'text="Books library"')
    first = _state(first_xml)
    second = _state(second_xml)
    done_id = next(a["id"] for a in first["actions"] if a["operation"] == "DONE")
    tap_id = next(a["id"] for a in first["actions"] if a["operation"] == "TAP")
    seen_history: list[list[dict[str, Any]]] = []
    choices = iter(
        (
            {"action_id": done_id, "operation": "DONE", "latency_ms": 1, "confidence": 1.0},
            {"action_id": tap_id, "operation": "TAP", "latency_ms": 1, "confidence": 1.0},
        )
    )

    def choose(*args, **kwargs):
        seen_history.append(args[2])
        return next(choices)

    monkeypatch.setattr(agent, "choose", choose)
    result = agent.run_jev(
        "Open the main library",
        lambda **kwargs: first,
        lambda **kwargs: {"next_observation": second},
        max_steps=3,
        completion_oracle=lambda observation, goal: (
            observation.fingerprint == second["fingerprint"]
        ),
    )

    assert result["status"] == "done"
    assert result["steps"] == 2
    assert result["trace"][0]["status"] == "done_rejected"
    assert seen_history[1][-1]["completion_verified"] is False
    assert seen_history[1][-1]["result"] == "completion_rejected"


def test_run_jev_passes_requested_backend_to_jev(monkeypatch) -> None:
    state = _state(XML)
    calls: list[str | None] = []
    done_id = next(a["id"] for a in state["actions"] if a["operation"] == "DONE")

    def choose(*args, **kwargs):
        calls.append(kwargs.get("backend"))
        return {
            "action_id": done_id,
            "operation": "DONE",
            "latency_ms": 1,
            "confidence": 1.0,
        }

    monkeypatch.setattr(agent, "choose", choose)
    result = agent.run_jev(
        "Go back",
        lambda **kwargs: state,
        lambda **kwargs: {},
        backend="openrouter",
    )

    assert result["status"] == "done"
    assert calls == ["openrouter"]


def test_run_jev_uses_caller_text_for_unique_focused_resource_id(monkeypatch) -> None:
    field_xml = XML.replace(
        'resource-id="" class="android.widget.EditText"',
        'resource-id="app:id/query" class="android.widget.EditText"',
        1,
    )
    state = _state(field_xml)
    field_id = next(
        action["id"]
        for action in state["actions"]
        if action["operation"] == "TYPE_TEXT"
    )
    typed: list[dict[str, Any]] = []

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        agent,
        "choose",
        lambda *args, **kwargs: {
            "action_id": field_id,
            "operation": "TYPE_TEXT",
            "latency_ms": 3,
            "confidence": 1.0,
        },
    )
    monkeypatch.setattr(
        agent,
        "field_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("helper must be skipped")),
    )

    result = agent.run_jev(
        "Search for The Hobbit",
        lambda **kwargs: state,
        lambda **kwargs: typed.append(kwargs) or {
            "next_observation": state,
            "timings_ms": {"wait_ms": 500, "action_ms": 1},
        },
        backend="openrouter",
        text_by_resource_id={"app:id/query": "The Hobbit"},
    )

    assert typed[0]["text"] == "The Hobbit"
    assert typed[0]["wait_seconds"] == 0.5
    assert result["trace"][0]["text_source"] == "caller"
    assert result["setup_timings_ms"]["credentials_ms"] >= 0
    assert "setup_timings_ms" not in result["trace"][0]
    assert "wait_ms" in result["trace"][0]["timings_ms"]


@pytest.mark.parametrize("ambiguous", [False, True])
def test_run_jev_rejects_unfocused_or_ambiguous_text_target(monkeypatch, ambiguous: bool) -> None:
    xml = XML.replace(
        'resource-id="" class="android.widget.EditText"',
        'resource-id="app:id/query" class="android.widget.EditText"',
        1,
    )
    state = _state(xml)
    field = next(node for node in state["nodes"] if node["class_name"].endswith("EditText"))
    if ambiguous:
        duplicate = dict(field, id="node-99", path="0/9")
        state["nodes"].append(duplicate)
    else:
        field["focused"] = False
    action = next(action for action in state["actions"] if action["operation"] == "TYPE_TEXT")
    monkeypatch.setattr(
        agent,
        "choose",
        lambda *args, **kwargs: {
            "action_id": action["id"],
            "operation": "TYPE_TEXT",
            "latency_ms": 1,
            "confidence": 1.0,
        },
    )
    executed: list[dict[str, Any]] = []

    result = agent.run_jev(
        "Search for The Hobbit",
        lambda **kwargs: state,
        lambda **kwargs: executed.append(kwargs) or {},
        text_by_resource_id={"app:id/query": "The Hobbit"},
    )

    assert result["status"] == "blocked"
    assert not executed
    assert "ambiguous" in result["reason"] if ambiguous else "focused" in result["reason"]


def test_run_jev_resolves_credentials_once_for_multiple_steps(monkeypatch) -> None:
    state = _state(XML)
    credentials: list[str | None] = []
    decisions = iter(("WAIT", "DONE"))
    monkeypatch.setattr(
        agent,
        "_select_backend",
        lambda backend=None: credentials.append(backend) or ("openrouter", "test-key"),
    )
    def choose(*args, **kwargs):
        operation = next(decisions)
        action = next(action for action in state["actions"] if action["operation"] == operation)
        return {
            "action_id": action["id"],
            "operation": operation,
            "latency_ms": 1,
            "confidence": 1.0,
        }

    monkeypatch.setattr(agent, "choose", choose)

    result = agent.run_jev(
        "Finish the task",
        lambda **kwargs: state,
        lambda **kwargs: {"next_observation": state, "timings_ms": {"wait_ms": 0}},
        max_steps=2,
    )

    assert result["status"] == "done"
    assert credentials == [None]


def test_run_jev_passes_shared_http_client_to_text_helper_and_records_fallback_timings(
    monkeypatch,
) -> None:
    state = _state(XML)
    action = next(action for action in state["actions"] if action["operation"] == "TYPE_TEXT")
    client = object()
    received: list[object] = []
    observed = iter((state, state | {"_timings_ms": {"capture_ms": 7, "parse_ms": 2}}))
    monkeypatch.setattr(
        agent,
        "choose",
        lambda *args, **kwargs: {
            "action_id": action["id"],
            "operation": "TYPE_TEXT",
            "latency_ms": 1,
            "confidence": 1.0,
        },
    )

    def text_helper(*args, http_client=None, **kwargs):
        received.append(http_client)
        return "The Hobbit", {"model": "test-helper"}

    monkeypatch.setattr(agent, "field_text", text_helper)
    result = agent.run_jev(
        "Search for The Hobbit",
        lambda **kwargs: next(observed),
        lambda **kwargs: {},
        http_client=client,
        max_steps=1,
    )

    assert received == [client]
    assert result["trace"][0]["timings_ms"]["capture_ms"] == 7
    assert result["trace"][0]["timings_ms"]["parse_ms"] == 2
