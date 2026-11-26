from jev_android_mcp.adb import AdbClient, AdbError


def test_press_enter_uses_android_enter_keycode(monkeypatch) -> None:
    client = AdbClient("emulator-5554")
    calls: list[tuple[str, ...]] = []

    def shell(*args: str, timeout: float = 20.0) -> str:
        calls.append(args)
        return ""

    monkeypatch.setattr(client, "shell", shell)

    assert client.press_enter() == ""
    assert calls == [("input", "keyevent", "KEYCODE_ENTER")]


def test_long_press_holds_one_point(monkeypatch) -> None:
    client = AdbClient("emulator-5554")
    calls: list[tuple[str, ...]] = []

    def shell(*args: str, timeout: float = 20.0) -> str:
        calls.append(args)
        return ""

    monkeypatch.setattr(client, "shell", shell)

    client.long_press(155, 55)

    assert calls == [("input", "swipe", "155", "55", "155", "55", "800")]


def test_hierarchy_uses_the_window_dump_and_falls_back_to_a_plain_dump(monkeypatch) -> None:
    client = AdbClient("emulator-5554")
    calls: list[tuple[str, ...]] = []
    plain = '<hierarchy rotation="0"><node text="Plain" /></hierarchy>'

    def run(args, timeout: float = 20.0) -> str:
        calls.append(tuple(args))
        if "--windows" in args:
            raise AdbError("unknown option --windows")
        return f"<?xml version='1.0' ?>{plain}UI hierchary dumped to: /dev/tty"

    monkeypatch.setattr(client, "run", run)

    assert client.ui_hierarchy_xml() == plain
    assert calls == [
        ("exec-out", "uiautomator", "dump", "--windows", "/dev/tty"),
        ("exec-out", "uiautomator", "dump", "/dev/tty"),
    ]


def test_hierarchy_returns_the_merged_window_dump(monkeypatch) -> None:
    client = AdbClient("emulator-5554")
    windows = (
        '<displays><display id="0"><window active="true" layer="0" type="TYPE_APPLICATION">'
        '<hierarchy rotation="0"><node text="Main" package="a" /></hierarchy></window>'
        "</display></displays>"
    )
    monkeypatch.setattr(client, "run", lambda args, timeout=20.0: windows)

    assert client.ui_hierarchy_xml() == (
        '<hierarchy rotation="0"><node text="Main" package="a" /></hierarchy>'
    )


def test_run_script_sends_one_quoted_shell_command(monkeypatch) -> None:
    client = AdbClient("emulator-5554")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(client, "run", lambda args, timeout=20.0: calls.append(tuple(args)) or "")

    client.run_script([["input", "motionevent", "DOWN", "1", "2"], ["sleep", "0.016"]], 5)

    assert calls == [("shell", "input motionevent DOWN 1 2 ; sleep 0.016")]
