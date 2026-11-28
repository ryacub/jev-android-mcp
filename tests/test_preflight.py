import pytest
from mcp.server.mcpserver.exceptions import ToolError

from jev_android_mcp import server
from jev_android_mcp.adb import AdbClient, Device

PACKAGE = "xyz.rayniyomi.dev"
BASE = "/data/app/~~alpha==/xyz.rayniyomi.dev-beta==/base.apk"
SPLIT = "/data/app/~~alpha==/xyz.rayniyomi.dev-beta==/split_config.arm64_v8a.apk"


@pytest.fixture
def responding_device(monkeypatch):
    monkeypatch.setenv("ANDROID_APP_PACKAGE", PACKAGE)
    monkeypatch.setattr(
        AdbClient,
        "list_devices",
        staticmethod(lambda adb_path="adb": [Device("emulator-5554", "device")]),
    )
    responses = {}
    calls = []

    def shell(self, *args, **kwargs):
        assert self.serial == "emulator-5554"
        calls.append(args)
        return responses[args]

    monkeypatch.setattr(AdbClient, "shell", shell)
    return responses, calls


def test_preflight_checks_boot_before_querying_package(responding_device):
    responses, calls = responding_device
    responses["getprop", "sys.boot_completed"] = "0\n"

    result = server.android_preflight(include_apk_sha256=True)

    assert result == {
        "serial": "emulator-5554",
        "package": PACKAGE,
        "status": "booting",
        "boot_completed": False,
        "installed": None,
        "version_code": None,
        "version_name": None,
        "apk_sha256": None,
    }
    assert calls == [("getprop", "sys.boot_completed")]


def test_preflight_requires_an_exact_installed_package(responding_device):
    responses, _ = responding_device
    responses["getprop", "sys.boot_completed"] = "1\n"
    responses["pm", "list", "packages", "--user", "current", PACKAGE] = f"package:{PACKAGE}.test\n"

    result = server.android_preflight()

    assert result["status"] == "package_missing"
    assert result["installed"] is False
    assert result["apk_sha256"] is None


def test_preflight_reports_version_and_optional_split_hashes(responding_device):
    responses, calls = responding_device
    responses["getprop", "sys.boot_completed"] = "1\n"
    responses["pm", "list", "packages", "--user", "current", PACKAGE] = f"package:{PACKAGE}\n"
    responses["dumpsys", "package", PACKAGE] = (
        f"Packages:\n  Package [{PACKAGE}] (a123):\n"
        "    versionCode=644 minSdk=26 targetSdk=36\n    versionName=0.18.1.513\n"
    )
    responses["pm", "path", "--user", "current", PACKAGE] = f"package:{BASE}\npackage:{SPLIT}\n"
    responses["sha256sum", BASE, SPLIT] = f"{'a' * 64}  {BASE}\n{'b' * 64}  {SPLIT}\n"

    without_hash = server.android_preflight()
    assert without_hash["version_code"] == 644
    assert without_hash["version_name"] == "0.18.1.513"
    assert without_hash["apk_sha256"] is None
    assert all(call[0] != "sha256sum" for call in calls)

    with_hash = server.android_preflight(serial="emulator-5554", include_apk_sha256=True)
    assert with_hash == without_hash | {
        "status": "ready",
        "installed": True,
        "apk_sha256": {"base.apk": "a" * 64, "split_config.arm64_v8a.apk": "b" * 64},
    }


def test_preflight_keeps_the_package_allowlist_and_serial_requirement(
    monkeypatch, responding_device
):
    monkeypatch.setattr(
        AdbClient,
        "list_devices",
        staticmethod(
            lambda adb_path="adb": [
                Device("emulator-5554", "device"),
                Device("emulator-5556", "device"),
            ]
        ),
    )
    with pytest.raises(ToolError, match="not in ANDROID_APP_PACKAGE"):
        server.android_preflight(package="com.example.other", serial="emulator-5554")
    with pytest.raises(ToolError, match="pass serial explicitly"):
        server.android_preflight()
