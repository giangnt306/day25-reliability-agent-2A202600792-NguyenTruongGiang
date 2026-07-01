from __future__ import annotations

import json
import random
from pathlib import Path

from reliability_lab.cache import ResponseCache, SharedRedisCache
from reliability_lab.circuit_breaker import CircuitBreaker
from reliability_lab.config import LabConfig, ScenarioConfig
from reliability_lab.gateway import ReliabilityGateway
from reliability_lab.metrics import RunMetrics
from reliability_lab.providers import FakeLLMProvider


def load_queries(path: str | Path = "data/sample_queries.jsonl") -> list[str]:
    queries: list[str] = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        queries.append(json.loads(line)["query"])
    return queries


def build_gateway(config: LabConfig, provider_overrides: dict[str, float] | None = None) -> ReliabilityGateway:
    providers = []
    for p in config.providers:
        fail_rate = provider_overrides.get(p.name, p.fail_rate) if provider_overrides else p.fail_rate
        providers.append(FakeLLMProvider(p.name, fail_rate, p.base_latency_ms, p.cost_per_1k_tokens))
    breakers = {
        p.name: CircuitBreaker(
            name=p.name,
            failure_threshold=config.circuit_breaker.failure_threshold,
            reset_timeout_seconds=config.circuit_breaker.reset_timeout_seconds,
            success_threshold=config.circuit_breaker.success_threshold,
        )
        for p in config.providers
    }
    cache: ResponseCache | SharedRedisCache | None = None
    if config.cache.enabled:
        if config.cache.backend == "redis":
            cache = SharedRedisCache(
                config.cache.redis_url,
                config.cache.ttl_seconds,
                config.cache.similarity_threshold,
            )
        else:
            cache = ResponseCache(config.cache.ttl_seconds, config.cache.similarity_threshold)
    return ReliabilityGateway(providers, breakers, cache)


def calculate_recovery_time_ms(gateway: ReliabilityGateway) -> float | None:
    """Derive mean recovery time (open → closed) from breaker transition logs.

    Recovery time is the wall-clock gap between a circuit opening and the next
    time it closes again — a direct measure of how long the system stayed in a
    degraded/fail-fast state. Averaged across every open→close cycle on every
    breaker. Returns None when no full recovery cycle was observed.
    """
    recovery_times_ms: list[float] = []
    for breaker in gateway.breakers.values():
        open_ts: float | None = None
        for entry in breaker.transition_log:
            if entry["to"] == "open":
                open_ts = float(entry["ts"])
            elif entry["to"] == "closed" and open_ts is not None:
                recovery_times_ms.append((float(entry["ts"]) - open_ts) * 1000)
                open_ts = None
    if not recovery_times_ms:
        return None
    return sum(recovery_times_ms) / len(recovery_times_ms)


def run_scenario(config: LabConfig, queries: list[str], scenario: ScenarioConfig) -> RunMetrics:
    """Run one named chaos scenario and collect reliability metrics.

    Replays a randomized load through a freshly built gateway (so circuit
    breaker state is isolated per scenario) and classifies each response by its
    route to derive availability, fallback rate, cache-hit rate, latency
    distribution, cost, and circuit-open counts.
    """
    gateway = build_gateway(config, scenario.provider_overrides or None)
    metrics = RunMetrics()

    for _ in range(config.load_test.requests):
        prompt = random.choice(queries)
        result = gateway.complete(prompt)

        metrics.total_requests += 1
        metrics.estimated_cost += result.estimated_cost

        if result.cache_hit:
            metrics.cache_hits += 1
            # A cache hit avoids a provider call — approximate the cost avoided.
            metrics.estimated_cost_saved += 0.001
            metrics.successful_requests += 1
        elif result.route == "fallback":
            metrics.fallback_successes += 1
            metrics.successful_requests += 1
        elif result.route == "static_fallback":
            metrics.static_fallbacks += 1
            metrics.failed_requests += 1
        else:  # "primary"
            metrics.successful_requests += 1

        if result.latency_ms > 0:
            metrics.latencies_ms.append(result.latency_ms)

    # Count every open transition across all breakers.
    metrics.circuit_open_count = sum(
        1
        for breaker in gateway.breakers.values()
        for entry in breaker.transition_log
        if entry["to"] == "open"
    )
    metrics.recovery_time_ms = calculate_recovery_time_ms(gateway)
    return metrics


def _scenario_passed(name: str, result: RunMetrics) -> bool:
    """Scenario-specific acceptance criteria (SLO-style assertions).

    Different failure injections imply different "healthy" outcomes:
    - primary_timeout_100: primary is dead, so the backup must absorb traffic
      and the circuit must actually open.
    - all_healthy: high availability with no circuit trips.
    - default: any scenario just needs high overall availability.
    """
    if name == "primary_timeout_100":
        return result.circuit_open_count >= 1 and result.availability >= 0.9
    if name == "primary_flaky_50":
        # Flaky primary: system should still stay highly available via fallback.
        return result.availability >= 0.9
    if name == "all_healthy":
        return result.availability >= 0.95 and result.circuit_open_count == 0
    if name == "total_outage":
        # Everything is down: success is *graceful* degradation — every request
        # is answered by the static fallback, nothing raises to the caller.
        return result.static_fallbacks == result.total_requests
    return result.availability >= 0.9


def run_simulation(config: LabConfig, queries: list[str]) -> RunMetrics:
    """Run all named scenarios from config, or a default run if none defined.

    TODO(student): Add a cache vs no-cache comparison scenario.
    Extend with your own custom scenarios (e.g., cost cap near limit).
    """
    if not config.scenarios:
        default_scenario = ScenarioConfig(name="default", description="baseline run")
        metrics = run_scenario(config, queries, default_scenario)
        metrics.scenarios = {"default": "pass" if metrics.successful_requests > 0 else "fail"}
        return metrics

    combined = RunMetrics()
    for scenario in config.scenarios:
        result = run_scenario(config, queries, scenario)

        # Per-scenario acceptance criteria — each failure mode has a different
        # "correct" behaviour, so we assert against the expected reliability.
        passed = _scenario_passed(scenario.name, result)
        combined.scenarios[scenario.name] = "pass" if passed else "fail"

        combined.total_requests += result.total_requests
        combined.successful_requests += result.successful_requests
        combined.failed_requests += result.failed_requests
        combined.fallback_successes += result.fallback_successes
        combined.static_fallbacks += result.static_fallbacks
        combined.cache_hits += result.cache_hits
        combined.circuit_open_count += result.circuit_open_count
        combined.estimated_cost += result.estimated_cost
        combined.estimated_cost_saved += result.estimated_cost_saved
        combined.latencies_ms.extend(result.latencies_ms)
        if result.recovery_time_ms is not None:
            if combined.recovery_time_ms is None:
                combined.recovery_time_ms = result.recovery_time_ms
            else:
                combined.recovery_time_ms = (combined.recovery_time_ms + result.recovery_time_ms) / 2

    return combined
