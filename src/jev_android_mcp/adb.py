"""Small, bounded ADB client used by MCP tools."""

from __future__ import annotations

import re
import shlex
import subprocess
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Literal


class AdbError(RuntimeError):
    """Raised when an ADB operation fails."""


@dataclass(frozen=True)
class Device:
    serial: str
    state: str
    details: str = ""


_DEVICE_LOCKS: dict[str, Lock] = {}
_DEVICE_LOCKS_GUARD = Lock()
_SENSITIVE_LOG_RE = re.compile(
    r"(?i)(\b(?:password|passcode|token|secret|authorization|cookie|api[_-]?key)\b\s*[:=]\s*)\S+"
)
_BEARER_RE = re.compile(r"(?i)(\bbearer\s+)\S+")


class AdbClient:
    """Execute explicit ADB operations for one optional device serial."""

    def __init__(self, serial: str | None = None, adb_path: str = "adb") -> None:
        self.serial = serial
        self.adb_path = adb_path

    def _command(self, args: Sequence[str]) -> list[str]:
        command = [self.adb_path]
        if self.serial:
            command.extend(("-s", self.serial))
        command.extend(args)
        return command

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Serialize multi-command operations for this selected device."""

        if not self.serial:
            raise AdbError("An explicit device serial is required for Android operations")
        with _DEVICE_LOCKS_GUARD:
            lock = _DEVICE_LOCKS.setdefault(self.serial, Lock())
        with lock:
            yield

    def run(self, args: Sequence[str], timeout: float = 20.0) -> str:
        """Run a fixed ADB subcommand and return stdout."""

        try:
            completed = subprocess.run(
                self._command(args),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise AdbError("adb was not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"adb timed out after {timeout:.1f}s: {' '.join(args)}") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise AdbError(f"adb {' '.join(args)} failed: {detail}")
        return completed.stdout

    @staticmethod
    def list_devices(adb_path: str = "adb") -> list[Device]:
        """List connected devices without selecting one first."""

        client = AdbClient(adb_path=adb_path)
        output = client.run(("devices", "-l"))
        devices: list[Device] = []
        for line in output.splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 2:
                devices.append(
                    Device(serial=fields[0], state=fields[1], details=" ".join(fields[2:]))
                )
        return devices

    @staticmethod
    def resolve_serial(serial: str | None = None, adb_path: str = "adb") -> str:
        """Select exactly one online device, never relying on implicit ADB selection."""

        devices = AdbClient.list_devices(adb_path)
        online = [device.serial for device in devices if device.state == "device"]
        if serial:
            if serial not in online:
                raise AdbError(f"Selected device is not online: {serial}")
            return serial
        if not online:
            raise AdbError("No online Android emulator/device is connected")
        if len(online) > 1:
            raise AdbError("Multiple online devices; pass serial explicitly")
        return online[0]

    def shell(self, *args: str, timeout: float = 20.0) -> str:
        """Run a specific shell subcommand supplied by trusted code."""

        # Send one quoted command string so Android's remote shell cannot reinterpret
        # untrusted values as operators or additional commands.
        return self.run(("shell", shlex.join(args)), timeout=timeout)

    def ui_hierarchy_xml(self, timeout: float = 30.0) -> str:
        """Capture the current UIAutomator hierarchy."""

        deadline = time.monotonic() + timeout

        def remaining() -> float:
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise AdbError(f"UI hierarchy capture exceeded {timeout:.1f}s")
            return seconds

        with self.locked():
            # /dev/tty keeps the capture in stdout and avoids stale files plus three
            # additional shell round trips. It also makes the benchmark measure
            # observation latency rather than emulator storage housekeeping.
            output = self.run(("exec-out", "uiautomator", "dump", "/dev/tty"), timeout=remaining())
            start = output.find("<hierarchy")
            end = output.rfind("</hierarchy>")
            if start < 0 or end < 0:
                raise AdbError("UIAutomator returned no hierarchy XML")
            return output[start : end + len("</hierarchy>")]

    def launch(self, package: str, activity: str | None = None) -> str:
        """Launch a package or explicit activity."""

        with self.locked():
            if activity:
                component = f"{package}/{activity}"
                return self.shell("am", "start", "-n", component)

            resolved = self.shell(
                "cmd",
                "package",
                "resolve-activity",
                "--brief",
                "-a",
                "android.intent.action.MAIN",
                "-c",
                "android.intent.category.LAUNCHER",
                package,
            )
            component = next(
                (line.strip() for line in reversed(resolved.splitlines()) if "/" in line), ""
            )
            if not component:
                raise AdbError(f"No launcher activity found for {package}")
            return self.shell("am", "start", "-n", component)

    def clear_app_data(self, package: str) -> str:
        """Clear one app's data; callers must explicitly request this operation."""

        with self.locked():
            return self.shell("pm", "clear", package)

    def tap(self, x: int, y: int) -> str:
        """Tap one coordinate on the selected device."""

        with self.locked():
            return self.shell("input", "tap", str(x), str(y))

    def type_text(self, text: str) -> str:
        """Type text without putting the text in a command log or exception."""

        if not text:
            raise AdbError("TYPE_TEXT requires non-empty text")
        with self.locked():
            try:
                return self.shell("input", "text", text)
            except AdbError:
                raise AdbError("adb input text failed") from None

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> str:
        """Swipe between two coordinates on the selected device."""

        with self.locked():
            return self.shell(
                "input",
                "swipe",
                str(x1),
                str(y1),
                str(x2),
                str(y2),
                str(duration_ms),
            )

    def back(self) -> str:
        """Send Android back to the selected device."""

        with self.locked():
            return self.shell("input", "keyevent", "KEYCODE_BACK")

    def home(self) -> str:
        """Send Android Home to the selected device."""

        with self.locked():
            return self.shell("input", "keyevent", "KEYCODE_HOME")

    def rotate(self, orientation: Literal["portrait", "landscape"]) -> str:
        """Set a fixed emulator orientation without relying on screenshots."""

        user_rotation = "0" if orientation == "portrait" else "1"
        with self.locked():
            self.shell("settings", "put", "system", "accelerometer_rotation", "0")
            return self.shell("settings", "put", "system", "user_rotation", user_rotation)

    def logcat(self, package: str, lines: int = 100) -> str:
        """Return a bounded logcat tail for the target package process."""

        bounded_lines = max(1, min(lines, 1000))
        pid = self.shell("pidof", package, timeout=10.0).strip().split()
        if not pid:
            return ""
        output = self.run(("logcat", "--pid", pid[0], "-d", "-t", str(bounded_lines)), timeout=30.0)
        output = _SENSITIVE_LOG_RE.sub(r"\1[REDACTED]", output)
        return _BEARER_RE.sub(r"\1[REDACTED]", output)
