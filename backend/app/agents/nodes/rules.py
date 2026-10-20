RULES = [
    {
        "name": "High DB latency",
        "signals": ["db_latency", "slow_queries"],
        "evidence": ["latency_ms > 3000"],
        "finding": "Database latency is elevated.",
        "next_steps": [
            "Check slow queries.",
            "Inspect DB CPU usage.",
            "Review recent schema changes."
        ]
    },
    {
        "name": "High error rate",
        "signals": ["error_rate", "5xx"],
        "evidence": ["error_rate > 0.05"],
        "finding": "Service is returning many errors.",
        "next_steps": [
            "Check recent deployments.",
            "Inspect logs for stack traces.",
            "Verify upstream dependencies."
        ]
    }
]
