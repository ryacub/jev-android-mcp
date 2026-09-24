import json
from typing import Any

from test_state import XML

from jev_android_mcp import benchmark, jev
from jev_android_mcp.models import Observation
from jev_android_mcp.state import parse_observation


def test_choose_uses_only_the_selected_operation_target(monkeypatch) -> None:
    observation = parse_observation(XML, "emulator-5554")
    tap_ids = [action.id for action in observation.actions if action.operation == "TAP"]

    def fake_post(body: dict[str, Any], api_key: str) -> dict[str, Any]:
        operation_ids = set(body["questions"]["operation"]["criteria"])
        operation_probabilities = {
            operation: 1.0 if operation == "TAP" else 0.0 for operation in operation_ids
        }
        target_probabilities = {
            action_id: 1.0 if action_id == tap_ids[0] else 0.0 for action_id in tap_ids
        }
        return {
            "model": "jev-test",
            "answers": {
                "operation": {
                    "choice": "TAP",
                    "probabilities": operation_probabilities,
                    "confidence": 1.0,
                },
                "tap_target": {
                    "choice": tap_ids[0],
                    "probabilities": target_probabilities,
                    "confidence": 1.0,
                },
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("JEV_BACKEND", "typesafe")
    monkeypatch.setattr(jev, "_post_json", fake_post)

    decision = jev.choose(observation, "Tap Search", [])

    assert decision["operation"] == "TAP"
    assert decision["action_id"] == tap_ids[0]
    assert decision["model"] == "jev-test"


def test_choose_rejects_invalid_model_target(monkeypatch) -> None:
    observation = Observation.model_validate(parse_observation(XML, "emulator-5554").model_dump())

    def fake_post(body: dict[str, Any], api_key: str) -> dict[str, Any]:
        operation_ids = set(body["questions"]["operation"]["criteria"])
        probabilities = {operation: 1.0 / len(operation_ids) for operation in operation_ids}
        return {
            "answers": {
                "operation": {
                    "choice": "TAP",
                    "probabilities": probabilities,
                    "confidence": 1.0,
                },
                "tap_target": {
                    "choice": "not-observed",
                    "probabilities": {"not-observed": 1.0},
                    "confidence": 1.0,
                },
            }
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("JEV_BACKEND", "typesafe")
    monkeypatch.setattr(jev, "_post_json", fake_post)

    try:
        jev.choose(observation, "Tap Search", [])
    except jev.JevError as exc:
        assert "Invalid Jev choice" in str(exc)
    else:
        raise AssertionError("invalid target should not be executable")


def test_choose_uses_openrouter_fallback(monkeypatch) -> None:
    observation = parse_observation(XML, "emulator-5554")

    class FakeResponse:
        status_code = 200
        is_error = False

        @staticmethod
        def json() -> dict[str, Any]:
            return {
                "model": "openai/gpt-5.6-luna",
                "choices": [
                    {"message": {"content": json.dumps({"operation": "BACK", "target": None})}}
                ],
            }

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("JEV_BACKEND", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(jev.httpx, "post", lambda *args, **kwargs: FakeResponse())

    decision = jev.choose(observation, "Go back", [])

    assert decision["operation"] == "BACK"
    assert decision["model"] == "openai/gpt-5.6-luna"


def test_backend_selection_keeps_both_options_available(monkeypatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test-key")
    monkeypatch.setattr(
        jev,
        "resolve_api_key",
        lambda provider, env_name: (
            "typesafe-test-key" if provider == "typesafe" else "openrouter-test-key"
        ),
    )

    monkeypatch.setenv("JEV_BACKEND", "typesafe")
    assert jev._select_backend() == ("typesafe", "typesafe-test-key")

    monkeypatch.setenv("JEV_BACKEND", "openrouter")
    assert jev._select_backend() == ("openrouter", "openrouter-test-key")

    monkeypatch.setenv("JEV_BACKEND", "auto")
    assert jev._select_backend() == ("openrouter", "openrouter-test-key")


def test_matched_decision_records_latency_without_executing(monkeypatch) -> None:
    observation = parse_observation(XML, "emulator-5554")
    calls: list[str] = []

    def fake_choose(*args, **kwargs) -> dict[str, Any]:
        calls.append(kwargs["backend"])
        return {
            "operation": "BACK",
            "action_id": next(
                action.id for action in observation.actions if action.operation == "BACK"
            ),
            "model": "test-model",
            "latency_ms": 12,
            "usage": {"input_tokens": 10},
        }

    monkeypatch.setattr(benchmark, "choose", fake_choose)

    record = benchmark.matched_decision(observation, "Go back", "openrouter")

    assert record["status"] == "success"
    assert record["operation"] == "BACK"
    assert calls == ["openrouter"]
    assert benchmark.summarize([record])[0]["median_decision_latency_ms"] == 12.0


def test_matched_decision_accepts_reusable_http_client(monkeypatch) -> None:
    observation = parse_observation(XML, "emulator-5554")
    client = object()
    received: list[object] = []

    def fake_choose(*args, **kwargs) -> dict[str, Any]:
        received.append(kwargs["http_client"])
        return {
            "operation": "BACK",
            "action_id": next(
                action.id for action in observation.actions if action.operation == "BACK"
            ),
            "model": "test-model",
            "latency_ms": 4,
            "usage": {},
        }

    monkeypatch.setattr(benchmark, "choose", fake_choose)

    record = benchmark.matched_decision(observation, "Go back", "openrouter", http_client=client)

    assert record["status"] == "success"
    assert received == [client]


def test_summarize_reports_success_rate_and_total_latency() -> None:
    records = [
        {
            "backend": "openrouter",
            "status": "success",
            "decision_latency_ms": 10,
            "total_latency_ms": 20,
        },
        {
            "backend": "openrouter",
            "status": "error",
            "decision_latency_ms": 30,
            "total_latency_ms": 40,
        },
    ]

    summary = benchmark.summarize(records)

    assert summary == [
        {
            "backend": "openrouter",
            "runs": 2,
            "successes": 1,
            "success_rate": 0.5,
            "median_decision_latency_ms": 10.0,
            "median_total_latency_ms": 20.0,
        }
    ]


def test_summarize_keeps_warm_and_cold_conditions_separate() -> None:
    records = [
        {
            "backend": "openrouter",
            "condition": "cold",
            "status": "success",
            "decision_latency_ms": 100,
            "total_latency_ms": 130,
        },
        {
            "backend": "openrouter",
            "condition": "warm",
            "status": "success",
            "decision_latency_ms": 20,
            "total_latency_ms": 50,
        },
    ]

    assert benchmark.summarize(records) == [
        {
            "backend": "openrouter",
            "condition": "cold",
            "runs": 1,
            "successes": 1,
            "success_rate": 1.0,
            "median_decision_latency_ms": 100.0,
            "median_total_latency_ms": 130.0,
        },
        {
            "backend": "openrouter",
            "condition": "warm",
            "runs": 1,
            "successes": 1,
            "success_rate": 1.0,
            "median_decision_latency_ms": 20.0,
            "median_total_latency_ms": 50.0,
        },
    ]


def test_external_decision_must_match_frozen_action() -> None:
    observation = parse_observation(XML, "emulator-5554")
    back = next(action for action in observation.actions if action.operation == "BACK")

    record = benchmark.record_external_decision(
        observation,
        "codex-luna-high",
        "BACK",
        back.id,
        321.4,
    )

    assert record["model"] == "codex-luna-high"
    assert record["decision_latency_ms"] == 321.4
