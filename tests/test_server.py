import asyncio
from dataclasses import dataclass, field

import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from test_state import XML

from jev_android_mcp import server
from jev_android_mcp.adb import AdbError, Device
from jev_android_mcp.state import parse_observation


@dataclass
class FakeClient:
    serial: str = "emulator-5554"
    calls: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)

    def ui_hierarchy_xml(self, timeout: float = 30.0) -> str:
        return XML

    def tap(self, x: int, y: int) -> str:
        self.calls.append(("tap", (x, y)))
        return ""

    def type_text(self, text: str) -> str:
        self.calls.append(("type_text", (text,)))
        return ""

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> str:
        self.calls.append(("swipe", (x1, y1, x2, y2, duration_ms)))
        return ""

    def back(self) -> str:
        self.calls.append(("back", ()))
        return ""

    def home(self) -> str:
        self.calls.append(("home", ()))
        return ""

    def rotate(self, orientation: str) -> str:
        self.calls.append(("rotate", (orientation,)))
        return ""


def _prepare(monkeypatch) -> tuple[FakeClient, dict]:
    monkeypatch.setenv("ANDROID_APP_PACKAGE", "com.example.reader")
    observation = parse_observation(XML, "emulator-5554")
    client = FakeClient()
    server._OBSERVATIONS.clear()
    server._remember(observation)
    monkeypatch.setattr(server, "_client", lambda serial: client)
    return client, observation.model_dump(mode="json")


def test_configured_package_is_required_and_validated(monkeypatch) -> None:
    monkeypatch.delenv("ANDROID_APP_PACKAGE", raising=False)
    with pytest.raises(ValueError, match="ANDROID_APP_PACKAGE"):
        server._configured_package()

    monkeypatch.setenv("ANDROID_APP_PACKAGE", "not a package")
    with pytest.raises(ValueError, match="ANDROID_APP_PACKAGE"):
        server._configured_package()

    monkeypatch.setenv("ANDROID_APP_PACKAGE", "com.example.reader")
    assert server._configured_package() == "com.example.reader"


def _action(observation: dict, operation: str) -> dict:
    return next(action for action in observation["actions"] if action["operation"] == operation)


@pytest.mark.parametrize(
    ("operation", "text", "scroll_direction", "expected_call"),
    [
        ("TAP", None, "up", "tap"),
        ("TYPE_TEXT", "hello", "up", "type_text"),
        ("SCROLL", None, "down", "swipe"),
        ("BACK", None, "up", "back"),
        ("HOME", None, "up", "home"),
        ("ROTATE_PORTRAIT", None, "up", "rotate"),
        ("ROTATE_LANDSCAPE", None, "up", "rotate"),
        ("WAIT", None, "up", None),
        ("DONE", None, "up", None),
        ("BLOCKED", None, "up", None),
    ],
)
def test_android_act_executes_each_bounded_operation(
    monkeypatch,
    operation: str,
    text: str | None,
    scroll_direction: str,
    expected_call: str | None,
) -> None:
    client, observation = _prepare(monkeypatch)
    action = _action(observation, operation)

    result = server.android_act(
        observation_id=observation["observation_id"],
        action_id=action["id"],
        text=text,
        scroll_direction=scroll_direction,
        wait_seconds=0,
        serial="emulator-5554",
    )

    assert result["operation"] == operation
    assert [call[0] for call in client.calls] == ([] if expected_call is None else [expected_call])


def test_device_list_preserves_adb_device_state(monkeypatch) -> None:
    monkeypatch.setattr(
        server.AdbClient,
        "list_devices",
        staticmethod(lambda: [Device("emulator-5554", "device", "model:test")]),
    )

    assert server.device_list() == [
        {"serial": "emulator-5554", "state": "device", "details": "model:test"}
    ]


def _call_tool(name: str, arguments: dict) -> object:
    return asyncio.run(server.mcp.call_tool(name, arguments))


def test_package_env_accepts_a_comma_separated_list(monkeypatch) -> None:
    monkeypatch.setenv("ANDROID_APP_PACKAGE", "com.example.reader, com.example.reader.dev")

    assert server._configured_package() == "com.example.reader"
    assert server._allowed_packages() == {"com.example.reader", "com.example.reader.dev"}
    assert server._validate_package("com.example.reader.dev") == "com.example.reader.dev"


def test_package_env_rejects_an_invalid_list_entry(monkeypatch) -> None:
    monkeypatch.setenv("ANDROID_APP_PACKAGE", "com.example.reader,not a package")
    with pytest.raises(ValueError, match="not a package"):
        server._allowed_packages()


def test_observe_accepts_any_listed_package(monkeypatch) -> None:
    _prepare(monkeypatch)
    monkeypatch.setenv("ANDROID_APP_PACKAGE", "com.example.other,com.example.reader")

    assert server.android_observe(serial="emulator-5554")["package"] == "com.example.reader"


def test_observe_error_names_foreground_and_allowed_packages(monkeypatch) -> None:
    _prepare(monkeypatch)
    monkeypatch.setenv("ANDROID_APP_PACKAGE", "com.example.release,com.example.dev")

    with pytest.raises(ToolError) as caught:
        _call_tool("android_observe", {"serial": "emulator-5554"})

    assert not isinstance(caught.value, UnexpectedToolError)
    message = str(caught.value)
    assert "com.example.reader" in message
    assert "com.example.release, com.example.dev" in message


def test_adb_failure_text_reaches_the_client(monkeypatch) -> None:
    _prepare(monkeypatch)

    def offline(serial):
        raise AdbError("device emulator-5554 is offline")

    monkeypatch.setattr(server, "_client", offline)

    with pytest.raises(ToolError, match="device emulator-5554 is offline") as caught:
        _call_tool("android_observe", {"serial": "emulator-5554"})
    assert not isinstance(caught.value, UnexpectedToolError)


def test_logcat_accepts_a_listed_package(monkeypatch) -> None:
    monkeypatch.setenv("ANDROID_APP_PACKAGE", "com.example.reader,com.example.reader.dev")

    class LogClient:
        def logcat(self, package: str, lines: int) -> str:
            return f"{package}:{lines}"

    monkeypatch.setattr(server, "_client", lambda serial: LogClient())

    result = server.android_logcat(lines=5, package="com.example.reader.dev")
    assert result["text"] == "com.example.reader.dev:5"
