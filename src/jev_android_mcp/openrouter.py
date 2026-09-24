"""Optional OpenRouter text helper for observed Android editable fields."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .credentials import resolve_api_key
from .jev import JevError
from .models import Action, Observation


def field_text(
    goal: str,
    observation: Observation,
    action: Action,
    history: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Generate exactly one field value; never guess when the key is absent."""

    api_key = resolve_api_key("openrouter", "OPENROUTER_API_KEY")
    if not api_key:
        raise JevError("OPENROUTER_API_KEY is not configured; no text was typed")
    body = {
        "model": os.environ.get("OPENROUTER_TEXT_MODEL", "inception/mercury-2.5"),
        "max_tokens": 256,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    'Return a JSON object with exactly one key, "text". '
                    "Return a value only when the user's goal supplies it; otherwise use null. "
                    "Do not include commentary or actions."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "goal": goal,
                        "field": {"label": action.label, "node_id": action.node_id},
                        "app": observation.package,
                        "recent_actions": history[-6:],
                    }
                ),
            },
        ],
    }
    try:
        response = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=25.0,
        )
    except httpx.HTTPError:
        raise JevError("OpenRouter connection failed; no text was typed") from None
    if response.is_error:
        raise JevError(f"OpenRouter returned HTTP {response.status_code}; no text was typed")
    try:
        content = response.json()["choices"][0]["message"]["content"]
        value = json.loads(content)["text"]
    except (KeyError, IndexError, TypeError, ValueError):
        raise JevError("OpenRouter returned no valid field value; no text was typed") from None
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise JevError("OpenRouter returned no valid field value; no text was typed")
    return value, {
        "model": body["model"],
        "usage": response.json().get("usage", {}),
    }
