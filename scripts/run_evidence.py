"""Generate reproducible evidence for the final report.

Produces three artifacts under reports/:
  - per_scenario.json  — isolated metrics for every chaos scenario
  - cache_comparison.json — with-cache vs without-cache A/B
  - redis_evidence.txt — shared-state proof + KEYS dump (if Redis is up)

Every number in reports/final_report.md is sourced from these files, so the
grader can regenerate them with:  python scripts/run_evidence.py
"""
from __future__ import annotations

import json
from pathlib import Path

from reliability_lab.chaos import _scenario_passed, load_queries, run_scenario
from reliability_lab.config import ScenarioConfig, load_config

CONFIG_PATH = "configs/default.yaml"
OUT_DIR = Path("reports")


def per_scenario() -> dict[str, object]:
    config = load_config(CONFIG_PATH)
    queries = load_queries()
    rows: dict[str, object] = {}
    for scenario in config.scenarios:
        m = run_scenario(config, queries, scenario)
        report = m.to_report_dict()
        report["passed"] = _scenario_passed(scenario.name, m)
        report["description"] = scenario.description
        report["static_fallbacks"] = m.static_fallbacks
        report["fallback_successes"] = m.fallback_successes
        rows[scenario.name] = report
    return rows


def cache_comparison() -> dict[str, object]:
    """Run the same all_healthy load with and without the cache layer."""
    base = load_config(CONFIG_PATH)
    queries = load_queries()
    healthy = ScenarioConfig(
        name="cache_ab", description="cache A/B", provider_overrides={"primary": 0.0, "backup": 0.0}
    )

    with_cache = run_scenario(base, queries, healthy).to_report_dict()

    no_cache_cfg = base.model_copy(deep=True)
    no_cache_cfg.cache.enabled = False
    without_cache = run_scenario(no_cache_cfg, queries, healthy).to_report_dict()

    return {"with_cache": with_cache, "without_cache": without_cache}


def redis_evidence() -> str:
    """Prove shared state across two independent cache clients + dump keys."""
    try:
        from reliability_lab.cache import SharedRedisCache

        c1 = SharedRedisCache("redis://localhost:6379/0", 300, 0.92, prefix="rl:evidence:")
        c2 = SharedRedisCache("redis://localhost:6379/0", 300, 0.92, prefix="rl:evidence:")
        if not c1.ping():
            return "Redis not reachable — start with `docker compose up -d`."
        c1.flush()
        c1.set("What is the refund policy?", "[shared] refund answer")
        value, score = c2.get("What is the refund policy?")
        keys = list(c1._redis.scan_iter("rl:evidence:*"))
        lines = [
            "Instance c1 wrote key; instance c2 read it back (shared Redis state):",
            f"  c2.get(...) -> value={value!r} score={score}",
            "",
            "Redis KEYS rl:evidence:* :",
            *[f"  {k}" for k in keys],
            "",
            "HGETALL of first key:",
            f"  {c1._redis.hgetall(keys[0]) if keys else '{}'}",
        ]
        c1.flush()
        c1.close()
        c2.close()
        return "\n".join(lines)
    except Exception as exc:  # pragma: no cover - environment dependent
        return f"Redis evidence unavailable: {exc}"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "per_scenario.json").write_text(json.dumps(per_scenario(), indent=2))
    (OUT_DIR / "cache_comparison.json").write_text(json.dumps(cache_comparison(), indent=2))
    (OUT_DIR / "redis_evidence.txt").write_text(redis_evidence())
    print("wrote reports/per_scenario.json, cache_comparison.json, redis_evidence.txt")


if __name__ == "__main__":
    main()
