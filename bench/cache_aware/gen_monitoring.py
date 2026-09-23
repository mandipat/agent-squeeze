"""Deterministic synthetic long agent session: periodic monitoring noise plus
a few high-signal tool outputs. Seed-fixed, byte-identical across runs.

Shape: a realistic cache scenario — the agent polls a service, producing
repeated boilerplate tool results (what the Jev FRAMING calls droppable:
heartbeats, health checks, repeated identical outputs), interleaved with a
few unique high-signal outputs (errors, test failures) that must be kept.
"""
import random

SEED = 20260923

SYSTEM = """You are a site-reliability agent. Monitor the payments service, \
investigate any anomaly, and report. Tool definitions:
healthcheck(service) -> 200 OK + latency; logs(service, tail) -> log lines; \
restart(service) -> status; deploy(version) -> status.""" * 10  # ~2k chars

BOILERPLATE = """{"tool": "healthcheck", "service": "payments", "status": 200, \
"latency_ms": 42, "uptime_s": 86400, "region": "us-west-2", \
"message": "all endpoints nominal, heartbeat ok"}"""

UNIQUE = [
    ('{"tool": "logs", "service": "payments", "tail": 50, "lines": ['
     '"ERROR 2026-09-22T23:01:11Z db connection pool exhausted (128/128)", '
     '"WARN 2026-09-22T23:01:12Z retry storm from checkout-svc", '
     '"ERROR 2026-09-22T23:01:13Z payment charge idempotent-replay failed"] }'),
    ('{"tool": "logs", "service": "payments", "tail": 50, "lines": ['
     '"TRACE deploy 7f3a2c canary 5%% -> 500 rate 0.4%%", '
     '"ERROR 2026-09-22T23:04:02Z canary pods CrashLoopBackOff x17"] }'),
    ('{"tool": "restart", "service": "payments", "status": "ok", '
     '"note": "pool recovered, latency p99 back to 180ms"}'),
]


def build(seed=SEED, noise_rounds=60):
    rng = random.Random(seed)
    msgs = [
        {"role": "system", "name": "", "content": SYSTEM},
        {"role": "user", "name": "",
         "content": "Monitor the payments service overnight and report anomalies."},
    ]
    # scatter 3 high-signal outputs among the noise
    signal_at = {rng.randrange(noise_rounds) for _ in range(3)}
    uniq = list(UNIQUE)
    rng.shuffle(uniq)
    for i in range(noise_rounds):
        msgs.append({"role": "assistant", "name": "",
                     "content": f"[tool call: healthcheck {{\"service\": \"payments\"}}]"})
        if i in signal_at and uniq:
            content = uniq.pop() * 12  # make it chunk-sized (~1.5k tokens)
        else:
            content = (BOILERPLATE + "\n") * 40  # ~1000 tokens of noise
        msgs.append({"role": "tool", "name": "healthcheck", "content": content})
    msgs.append({"role": "user", "name": "", "content": "Summarize the night."})
    return msgs
