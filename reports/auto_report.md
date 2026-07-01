# Day 10 Reliability Final Report

## Metrics Summary

| Metric | Value |
|---|---:|
| total_requests | 400 |
| availability | 0.75 |
| error_rate | 0.25 |
| latency_p50_ms | 270.18 |
| latency_p95_ms | 314.11 |
| latency_p99_ms | 318.01 |
| fallback_success_rate | 0.4083 |
| cache_hit_rate | 0.4725 |
| circuit_open_count | 11 |
| recovery_time_ms | 2410.5403423309326 |
| estimated_cost | 0.04742 |
| estimated_cost_saved | 0.189 |

## Chaos Scenarios

| Scenario | Status |
|---|---|
| primary_timeout_100 | pass |
| primary_flaky_50 | pass |
| all_healthy | pass |
| total_outage | pass |

## Analysis TODO(student)

Explain what failed, why the fallback path worked or did not work, and what you would change before production.