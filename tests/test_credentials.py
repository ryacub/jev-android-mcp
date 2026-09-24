import json
import os
import subprocess

from jev_android_mcp import credentials
from jev_android_mcp.credentials import resolve_api_key


def test_resolve_api_key_reads_pi_auth_file_before_environment(tmp_path, monkeypatch) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"openrouter": {"type": "api_key", "key": "stored"}}))
    os.chmod(auth_path, 0o600)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "environment")

    assert resolve_api_key("openrouter", "OPENROUTER_API_KEY") == "stored"


def test_resolve_api_key_supports_typesafe_pi_auth_file(tmp_path, monkeypatch) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"typesafe": {"type": "api_key", "key": "stored"}}))
    os.chmod(auth_path, 0o600)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("TYPESAFE_API_KEY", "environment")

    assert resolve_api_key("typesafe", "TYPESAFE_API_KEY") == "stored"


def test_resolve_api_key_uses_environment_when_auth_file_is_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "environment")

    assert resolve_api_key("openrouter", "OPENROUTER_API_KEY") == "environment"


def test_resolve_api_key_rejects_group_or_world_readable_auth_file(tmp_path, monkeypatch) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"openrouter": {"type": "api_key", "key": "stored"}}))
    os.chmod(auth_path, 0o644)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "environment")

    assert resolve_api_key("openrouter", "OPENROUTER_API_KEY") == "environment"


def test_resolve_api_key_supports_allowlisted_pi_keychain_command(tmp_path, monkeypatch) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "openrouter": {
                    "type": "api_key",
                    "key": "!security find-generic-password -ws openrouter",
                }
            }
        )
    )
    os.chmod(auth_path, 0o600)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    monkeypatch.setattr(
        credentials.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="keychain-value\n", stderr=""
        ),
    )

    assert resolve_api_key("openrouter", "OPENROUTER_API_KEY") == "keychain-value"


def test_resolve_api_key_rejects_non_keychain_commands(tmp_path, monkeypatch) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"openrouter": {"type": "api_key", "key": "!echo secret"}}))
    os.chmod(auth_path, 0o600)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "environment")

    assert resolve_api_key("openrouter", "OPENROUTER_API_KEY") == "environment"


def test_resolve_api_key_reads_direct_openrouter_keychain_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(credentials, "_run_openrouter_keychain_query", lambda: "password-value")

    assert resolve_api_key("openrouter", "OPENROUTER_API_KEY") == "password-value"


def test_resolve_api_key_reads_direct_typesafe_keychain_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(credentials, "_run_keychain_query", lambda service: "typesafe-password")

    assert resolve_api_key("typesafe", "TYPESAFE_API_KEY") == "typesafe-password"
