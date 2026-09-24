"""Credential lookup compatible with the local Pi auth-file convention."""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
import subprocess
from pathlib import Path
from typing import Any

_ENV_REFERENCE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$|^\$([A-Za-z_][A-Za-z0-9_]*)$")


def auth_file_path() -> Path:
    """Return the Pi-compatible auth file path without creating it."""

    agent_dir = os.environ.get("PI_CODING_AGENT_DIR")
    if agent_dir:
        return Path(agent_dir).expanduser() / "auth.json"
    return Path.home() / ".pi" / "agent" / "auth.json"


def _resolve_key_value(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    match = _ENV_REFERENCE.fullmatch(value)
    if match:
        return os.environ.get(match.group(1) or match.group(2)) or None
    if value.startswith("!"):
        return _run_allowlisted_keychain_query(value[1:])
    return value


def _run_allowlisted_keychain_query(command: str) -> str | None:
    """Resolve Pi's macOS Keychain form without invoking a shell."""

    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if len(tokens) < 2 or tokens[:2] != ["security", "find-generic-password"]:
        return None

    args = tokens[:2]
    has_password_output = False
    index = 2
    while index < len(tokens):
        token = tokens[index]
        if token == "-w":
            has_password_output = True
            args.append(token)
        elif token in {"-s", "-a"}:
            index += 1
            if index >= len(tokens) or tokens[index].startswith("-"):
                return None
            args.extend((token, tokens[index]))
        elif token == "-ws":
            has_password_output = True
            index += 1
            if index >= len(tokens) or tokens[index].startswith("-"):
                return None
            args.extend(("-w", "-s", tokens[index]))
        else:
            return None
        index += 1
    if not has_password_output:
        return None
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    return value or None


def _run_openrouter_keychain_query() -> str | None:
    """Read the exact OpenRouter item exposed by macOS Passwords/Keychain."""

    return _run_keychain_query("OPENROUTER_API_KEY")


def _run_typesafe_keychain_query() -> str | None:
    """Read the exact TypeSafe item exposed by macOS Passwords/Keychain."""

    return _run_keychain_query("TYPESAFE_API_KEY")


def _run_keychain_query(service: str) -> str | None:
    """Read one of the explicitly supported provider keychain items."""

    if service not in {"OPENROUTER_API_KEY", "TYPESAFE_API_KEY"}:
        return None
    return _run_allowlisted_keychain_query(
        f"security find-generic-password -s {service} -w"
    )


def _auth_file_key(path: Path, provider: str) -> str | None:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            return None
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    entry = data.get(provider) if isinstance(data, dict) else None
    if not isinstance(entry, dict) or entry.get("type") != "api_key":
        return None
    return _resolve_key_value(entry.get("key"))


def resolve_api_key(provider: str, env_name: str) -> str | None:
    """Resolve a provider key from Pi, the environment, or macOS Passwords."""

    stored = _auth_file_key(auth_file_path(), provider)
    if stored:
        return stored
    environment = os.environ.get(env_name)
    if environment:
        return environment
    if provider == "openrouter":
        return _run_openrouter_keychain_query()
    if provider == "typesafe":
        return _run_typesafe_keychain_query()
    return None
