import pytest
from mcp.server.mcpserver.exceptions import ToolError
from test_server import XML, _action, _prepare

from jev_android_mcp import server
from jev_android_mcp.adb import AdbError

LOADED = XML.replace('text="Search"', 'text="Episode 1"')


def test_observe_is_compact_by_default(monkeypatch) -> None:
    _prepare(monkeypatch)

    payload = server.android_observe()

    assert "nodes" not in payload
    assert payload["screen_text"] == ["Search"]
    assert set(payload["actions"][0]) == {"id", "operation", "label"}


def test_observe_full_detail_keeps_nodes(monkeypatch) -> None:
    _prepare(monkeypatch)

    assert len(server.android_observe(detail="full")["nodes"]) == 5


def test_compact_screen_text_skips_password_fields(monkeypatch) -> None:
    client, _ = _prepare(monkeypatch)
    client.screens = [
        XML.replace('text="Search"', 'text="hunter2"').replace(
            'password="false"\n          bounds="[10,10]',
            'password="true"\n          bounds="[10,10]',
        )
    ]

    assert "hunter2" not in server.android_observe()["screen_text"]


def test_act_returns_a_compact_next_observation(monkeypatch) -> None:
    _, observation = _prepare(monkeypatch)

    result = server.android_act(
        observation_id=observation["observation_id"],
        action_id=_action(observation, "BACK")["id"],
        wait_seconds=0,
    )

    assert "nodes" not in result["next_observation"]


@pytest.mark.parametrize(
    ("direction", "expected"),
    [("left", (864, 1070, 216, 1070, 300)), ("right", (216, 1070, 864, 1070, 300))],
)
def test_scroll_swipes_horizontally(monkeypatch, direction: str, expected: tuple) -> None:
    client, observation = _prepare(monkeypatch)

    server.android_act(
        observation_id=observation["observation_id"],
        action_id=_action(observation, "SCROLL")["id"],
        scroll_direction=direction,
        wait_seconds=0,
    )

    assert client.calls == [("swipe", expected)]


def test_press_key_sends_an_allowlisted_key(monkeypatch) -> None:
    client, observation = _prepare(monkeypatch)

    result = server.android_press_key(
        observation_id=observation["observation_id"], key="DEL", repeat=3, wait_seconds=0
    )

    assert client.calls == [("press_keys", ("KEYCODE_DEL", 3))]
    assert "nodes" not in result["next_observation"]


@pytest.mark.parametrize(("key", "repeat"), [("POWER", 1), ("DEL", 0), ("DEL", 51)])
def test_press_key_rejects_unlisted_keys_and_bad_repeats(monkeypatch, key, repeat) -> None:
    client, observation = _prepare(monkeypatch)

    with pytest.raises(ToolError):
        server.android_press_key(
            observation_id=observation["observation_id"], key=key, repeat=repeat
        )
    assert client.calls == []


def test_press_key_needs_the_latest_observation(monkeypatch) -> None:
    client, _ = _prepare(monkeypatch)

    with pytest.raises(ToolError):
        server.android_press_key(observation_id="stale", key="SPACE")
    assert client.calls == []


def test_open_uri_targets_the_configured_package(monkeypatch) -> None:
    client, _ = _prepare(monkeypatch)

    result = server.android_open_uri("example://item/42", wait_seconds=0)

    assert client.calls == [("open_uri", ("example://item/42", "com.example.reader"))]
    assert "nodes" not in result["next_observation"]


@pytest.mark.parametrize(
    ("uri", "package"),
    [("not a uri", None), ("example://x", "com.example.other"), ("", None)],
)
def test_open_uri_rejects_bad_uris_and_unlisted_packages(monkeypatch, uri, package) -> None:
    client, _ = _prepare(monkeypatch)

    with pytest.raises(ToolError):
        server.android_open_uri(uri, package=package)
    assert client.calls == []


def test_open_uri_reports_an_activity_manager_error(monkeypatch) -> None:
    client, _ = _prepare(monkeypatch)
    client.start_output = "Error: Activity not started, unable to resolve Intent"

    with pytest.raises(ToolError, match="unable to resolve"):
        server.android_open_uri("example://missing", wait_seconds=0)


def test_wait_for_returns_when_text_appears(monkeypatch) -> None:
    client, _ = _prepare(monkeypatch)
    client.screens = [XML, XML, LOADED]
    monkeypatch.setattr(server.time, "sleep", lambda seconds: None)

    result = server.android_wait_for("episode 1", timeout_seconds=30)

    assert result["status"] == "matched"
    assert result["polls"] == 3
    assert "Episode 1" in result["observation"]["screen_text"]


def test_wait_for_absent_returns_when_text_is_gone(monkeypatch) -> None:
    client, _ = _prepare(monkeypatch)
    client.screens = [XML, LOADED]
    monkeypatch.setattr(server.time, "sleep", lambda seconds: None)

    assert server.android_wait_for("Search", absent=True)["polls"] == 2


def test_wait_for_times_out(monkeypatch) -> None:
    _prepare(monkeypatch)
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(server.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(server.time, "sleep", lambda seconds: None)

    result = server.android_wait_for("never", timeout_seconds=12)

    assert result["status"] == "timed_out"


def test_wait_for_observation_can_be_acted_on(monkeypatch) -> None:
    client, _ = _prepare(monkeypatch)
    observation = server.android_wait_for("Search")["observation"]

    server.android_act(
        observation_id=observation["observation_id"],
        action_id=_action(observation, "BACK")["id"],
        wait_seconds=0,
    )

    assert client.calls == [("back", ())]


def test_wait_for_keeps_polling_through_a_capture_error(monkeypatch) -> None:
    client, _ = _prepare(monkeypatch)
    client.screens = [AdbError("UIAutomator returned no hierarchy XML"), LOADED]
    monkeypatch.setattr(server.time, "sleep", lambda seconds: None)

    result = server.android_wait_for("Episode 1")

    assert (result["status"], result["polls"]) == ("matched", 2)


def test_wait_for_reports_the_capture_error_when_it_never_clears(monkeypatch) -> None:
    client, _ = _prepare(monkeypatch)
    client.screens = [AdbError("UIAutomator returned no hierarchy XML")]
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(server.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(server.time, "sleep", lambda seconds: None)

    with pytest.raises(ToolError, match="no hierarchy XML"):
        server.android_wait_for("Episode 1", timeout_seconds=12)
