"""Read-only matched-decision benchmark for Jev backends."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx

from .jev import JevError, choose
from .models import Observation
from .server import android_observe


def matched_decision(
    observation: Observation,
    goal: str,
    backend: str,
    history: list[dict[str, Any]] | None = None,
    http_client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Measure one backend on one fixed observation without executing its action."""

    started = time.perf_counter()
    try:
        decision = choose(
            observation,
            goal,
            history or [],
            backend=backend,
            http_client=http_client,
        )
    except JevError as exc:
        return {
            "status": "error",
            "backend": backend,
            "observation_id": observation.observation_id,
            "fingerprint": observation.fingerprint,
            "decision_latency_ms": round((time.perf_counter() - started) * 1000),
            "error": str(exc),
        }
    return {
        "status": "success",
        "backend": backend,
        "observation_id": observation.observation_id,
        "fingerprint": observation.fingerprint,
        "operation": decision["operation"],
        "action_id": decision["action_id"],
        "model": decision.get("model"),
        "decision_latency_ms": decision["latency_ms"],
        "usage": decision.get("usage", {}),
    }


def record_external_decision(
    observation: Observation,
    backend: str,
    operation: str,
    action_id: str,
    decision_latency_ms: float,
    model: str = "codex-luna-high",
) -> dict[str, Any]:
    """Validate a decision made outside this process against a frozen observation."""

    action = next(
        (candidate for candidate in observation.actions if candidate.id == action_id), None
    )
    if action is None or action.operation != operation:
        raise ValueError("External decision does not match an offered observation action")
    if decision_latency_ms < 0:
        raise ValueError("decision_latency_ms must be non-negative")
    return {
        "status": "success",
        "backend": backend,
        "observation_id": observation.observation_id,
        "fingerprint": observation.fingerprint,
        "operation": operation,
        "action_id": action_id,
        "model": model,
        "decision_latency_ms": round(decision_latency_ms, 1),
        "usage": {},
    }


def summarize(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize successful decision latency by backend."""

    grouped: dict[tuple[str, str | None], list[float]] = {}
    total_grouped: dict[tuple[str, str | None], list[float]] = {}
    totals: dict[tuple[str, str | None], int] = {}
    for record in records:
        backend = str(record["backend"])
        condition = str(record["condition"]) if "condition" in record else None
        key = (backend, condition)
        totals[key] = totals.get(key, 0) + 1
        if record.get("status") == "success":
            grouped.setdefault(key, []).append(float(record["decision_latency_ms"]))
            if record.get("total_latency_ms") is not None:
                total_grouped.setdefault(key, []).append(
                    float(record["total_latency_ms"])
                )
    summary = []
    for backend, condition in sorted(totals):
        key = (backend, condition)
        latencies = grouped.get(key, [])
        item = {
            "backend": backend,
            "runs": totals[key],
            "successes": len(latencies),
            "success_rate": round(len(latencies) / totals[key], 3),
            "median_decision_latency_ms": (
                round(statistics.median(latencies), 1) if latencies else None
            ),
            "median_total_latency_ms": (
                round(statistics.median(total_grouped[key]), 1)
                if total_grouped.get(key)
                else None
            ),
        }
        if condition is not None:
            item["condition"] = condition
            item = {
                "backend": item["backend"],
                "condition": item["condition"],
                **{
                    key: value
                    for key, value in item.items()
                    if key not in {"backend", "condition"}
                },
            }
        summary.append(item)
    return summary


def run_matched_trials(
    goal: str,
    serial: str | None,
    repeats: int,
    backends: list[str],
    conditions: list[str],
    warmup: int = 0,
) -> list[dict[str, Any]]:
    """Run repeated frozen-observation decisions with cold or reused HTTP sessions."""

    records: list[dict[str, Any]] = []
    for condition in conditions:
        http_client = httpx.Client(http2=True) if condition == "warm" else None
        try:
            for _ in range(warmup if condition == "warm" else 0):
                observation = Observation.model_validate(android_observe(serial=serial))
                for backend in backends:
                    matched_decision(observation, goal, backend, http_client=http_client)
            for trial in range(1, repeats + 1):
                started = time.perf_counter()
                capture_started = time.perf_counter()
                observation = Observation.model_validate(android_observe(serial=serial))
                capture_latency_ms = round((time.perf_counter() - capture_started) * 1000)
                for backend in backends:
                    record = matched_decision(
                        observation,
                        goal,
                        backend,
                        http_client=http_client,
                    )
                    record.update(
                        {
                            "trial": trial,
                            "condition": condition,
                            "capture_latency_ms": capture_latency_ms,
                            "total_latency_ms": round((time.perf_counter() - started) * 1000),
                        }
                    )
                    records.append(record)
        finally:
            if http_client is not None:
                http_client.close()
    return records


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--serial")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--backend",
        action="append",
        choices=("typesafe", "openrouter"),
        help="Repeatable; defaults to both providers.",
    )
    parser.add_argument(
        "--condition",
        action="append",
        choices=("cold", "warm"),
        help="Repeatable; warm reuses one HTTP/2 client, cold creates requests independently.",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=0,
        help="Unrecorded warm-condition decisions used to establish a reused connection.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20:
        parser.error("--repeats must be between 1 and 20")
    if not 0 <= args.warmup <= 5:
        parser.error("--warmup must be between 0 and 5")
    return args


def main() -> None:
    """Capture matched read-only decisions and print a JSON summary."""

    args = _arguments()
    backends = args.backend or ["typesafe", "openrouter"]
    conditions = args.condition or ["cold"]
    records = run_matched_trials(
        args.goal,
        args.serial,
        args.repeats,
        backends,
        conditions,
        args.warmup,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    print(json.dumps({"summary": summarize(records), "records": records}, indent=2))


if __name__ == "__main__":
    main()
