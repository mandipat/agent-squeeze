"""Two-tier chunking benchmark (deterministic — no Jev, no paid calls).

Fixture: one error-dense tool result (traceback + log noise + one NEEDLE
signal line), one prose tool result (filler + one NEEDLE quoted fact),
plus user/assistant turns that are always kept.

Stub judge: keeps a chunk iff it contains a NEEDLE marker (perfect judge),
drops everything else. With a perfect judge the comparison is pure
chunking-policy: finer chunks can only drop more noise, never lose signal.

Compares:
  single-tier: every tool result chunked at CHUNK_CHARS (6000)
  two-tier:    error-dense results chunked at ERROR_CHUNK_CHARS (1500)

Metrics per mode: token reduction %, Jev judge calls (= chunks), needle
recall (each NEEDLE line must appear verbatim in the squeezed output).

Run: python bench_chunk_tiers.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agent_squeeze import jev, squeeze  # noqa: E402
from agent_squeeze.messages import estimate_tokens  # noqa: E402

NEEDLE_ERR = "NEEDLE-TRACE-4417: KeyError: 'session_id' in auth.py:212"
NEEDLE_PROSE = "NEEDLE-QUOTE-9021: the refund policy allows 30 days"

# ~9k chars: one signal line buried in frame lines + repeated noise.
_error_noise = (
    '  File "/app/src/server.py", line 88, in handle\n'
    '  File "/app/src/router.py", line 301, in dispatch\n'
    '    resp = handler(ctx)\n'
    '  File "/app/src/auth.py", line 190, in handler\n'
    '    user = lookup(session)\n'
    'INFO:worker:heartbeat ok attempt=%d\n' * 60
)
ERROR_RESULT = (
    "pytest tests/test_auth.py::test_session -x\n"
    "FAILED tests/test_auth.py::test_session - AssertionError\n"
    "Traceback (most recent call last):\n"
    + _error_noise.replace("%d", "7")[:4200]
    + f"{NEEDLE_ERR}\n"
    + _error_noise.replace("%d", "12")[:4200]
)

# ~9k chars: one signal fact buried in filler prose.
PROSE_RESULT = (
    ("The onboarding flow completed for the workspace. "
     "All integration tests passed on the staging environment. "
     "The team reviewed the changelog and approved the release. "
     "Metrics show latency within the SLO for the last seven days. ") * 45
    + f" {NEEDLE_PROSE}. "
    + ("Backfill jobs finished without retries. "
       "The dashboard renders all panels under two seconds. ") * 45
)

TASK = "Fix the failing auth session test and confirm the refund policy."
MESSAGES = [
    {"role": "user", "content": TASK},
    {"role": "assistant", "content": "I'll run the auth tests and check docs."},
    {"role": "tool", "content": ERROR_RESULT},
    {"role": "assistant", "content": "The test fails; now reading the docs page."},
    {"role": "tool", "content": PROSE_RESULT},
    {"role": "assistant", "content": "Found both: the auth KeyError and the refund window."},
]

CALLS = []  # (chunks) per stubbed score_chunks call


def stub_score_chunks(chunks, task):
    """Perfect judge: keep exactly the chunks holding a needle."""
    CALLS.append(chunks)
    probs = [0.95 if ("NEEDLE-TRACE-4417" in c or "NEEDLE-QUOTE-9021" in c)
             else 0.05 for c in chunks]
    return probs, 0.0


def run_mode(two_tier):
    CALLS.clear()
    out, stats = squeeze.squeeze_transcript(
        [dict(m) for m in MESSAGES], TASK, two_tier=two_tier)
    body = "\n".join(m["content"] for m in out)
    recall = all(n in body for n in (NEEDLE_ERR, NEEDLE_PROSE))
    return {
        "reduction_pct": stats["reduction_pct"],
        "judge_calls": len(CALLS[0]) if CALLS else 0,
        "recall": recall,
    }


def main():
    real = jev.score_chunks
    jev.score_chunks = stub_score_chunks
    try:
        single = run_mode(False)
        two = run_mode(True)
    finally:
        jev.score_chunks = real

    print(f"{'mode':<10} {'reduction%':>10} {'judge_calls':>11} {'recall':>7}")
    for name, r in (("single", single), ("two-tier", two)):
        print(f"{name:<10} {r['reduction_pct']:>10.2f} {r['judge_calls']:>11} "
              f"{r['recall']!s:>7}")
    d = two["reduction_pct"] - single["reduction_pct"]
    print(f"\nreduction delta: {d:+.2f} pts (recall {'2/2' if two['recall'] and single['recall'] else 'BROKEN'})")


if __name__ == "__main__":
    main()
