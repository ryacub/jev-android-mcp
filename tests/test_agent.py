from typing import Any

from test_state import XML

from jev_android_mcp import agent
from jev_android_mcp.completion import selected_tab_completion
from jev_android_mcp.state import parse_observation


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
