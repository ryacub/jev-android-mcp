from jev_android_mcp.adb import AdbClient


def test_press_enter_uses_android_enter_keycode(monkeypatch) -> None:
    client = AdbClient("emulator-5554")
    calls: list[tuple[str, ...]] = []

    def shell(*args: str, timeout: float = 20.0) -> str:
        calls.append(args)
        return ""

    monkeypatch.setattr(client, "shell", shell)

    assert client.press_enter() == ""
    assert calls == [("input", "keyevent", "KEYCODE_ENTER")]
