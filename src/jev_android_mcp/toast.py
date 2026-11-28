"""Bounded accessibility event capture for an opt-in app action."""

from __future__ import annotations

import os
import re
import select
import subprocess
import time
from collections.abc import Callable
from contextlib import suppress

from .adb import AdbClient, AdbError

_AUTOMATION = "Ui Automation["
_EVENT = re.compile(
    r"^(?:\S+ \S+ )?EventType: TYPE_NOTIFICATION_STATE_CHANGED;"
    r" EventTime: \d+; PackageName: ([\w.]+);"
    r".*?\[ ClassName: android\.widget\.Toast; Text: \[(.*?)\]; ContentDescription:"
)


def parse_toast(line: str, package: str) -> str | None:
    """Return one line of toast text for the exact package."""
    match = _EVENT.match(line)
    return match[2][:512] if match and match[1] == package and match[2] else None


def _automation_active(client: AdbClient) -> bool:
    return _AUTOMATION in client.shell("dumpsys", "accessibility", timeout=2.0)


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _wait_for_toast(output_fd: int, package: str, timeout_seconds: float) -> dict[str, str]:
    deadline = time.monotonic() + timeout_seconds
    pending = b""
    while (remaining := deadline - time.monotonic()) > 0:
        if not select.select([output_fd], [], [], remaining)[0]:
            break
        data = os.read(output_fd, 4096)
        if not data:
            return {"status": "unavailable"}
        lines = (pending + data).split(b"\n")
        pending = lines.pop()
        if len(pending) > 8192:
            pending = b""
        for line in lines:
            text = parse_toast(line.decode("utf-8", errors="replace"), package)
            if text is not None:
                return {"status": "observed", "text": text}
    return {"status": "not_observed"}


def capture_toast(
    client: AdbClient, package: str, action: Callable[[], object], timeout_seconds: float
) -> dict[str, str]:
    """Arm, run one action, and report the first matching toast event."""
    with client.locked():
        if _automation_active(client):
            raise AdbError("An accessibility automation session is already active on this device")
        with client.start_accessibility_events() as process:
            remote_pid = None
            try:
                if process.stdout is None:
                    raise AdbError("Accessibility event stream has no output")
                fd = process.stdout.fileno()
                if not select.select([fd], [], [], 2.0)[0]:
                    raise AdbError("Android did not start the accessibility event command")
                header = re.fullmatch(
                    rb"JEV_EVENT_PID:([1-9][0-9]*)", process.stdout.readline().strip()
                )
                if not header:
                    raise AdbError("Android did not report the accessibility event process ID")
                remote_pid = header[1].decode("ascii")
                deadline = time.monotonic() + 3.0
                while not _automation_active(client):
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise AdbError("Unable to arm accessibility events before the action")
                    time.sleep(0.05)
                # Stock uiautomator registers the connection just before it installs its listener.
                time.sleep(0.1)
                if process.poll() is not None:
                    raise AdbError("Accessibility event stream exited before the action")
                if select.select([fd], [], [], 0)[0]:
                    os.read(fd, 65536)
                action()
                return _wait_for_toast(fd, package, timeout_seconds)
            finally:
                if remote_pid is not None and process.poll() is None:
                    # Closing the host adb process can leave UiAutomation registered on Android.
                    with suppress(AdbError):
                        client.shell("kill", "-TERM", remote_pid, timeout=2.0)
                _stop(process)
                if remote_pid is not None:
                    deadline = time.monotonic() + 1.0
                    with suppress(AdbError):
                        while time.monotonic() < deadline and _automation_active(client):
                            time.sleep(0.05)
