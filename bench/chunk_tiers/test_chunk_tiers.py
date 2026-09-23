"""Tests for two-tier chunking in agent_squeeze.squeeze.

Self-contained runner (no pytest in this env). The Jev judge is stubbed
(deterministic) — no Jev, no paid calls.

Run: python3 test_chunk_tiers.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agent_squeeze import jev, squeeze  # noqa: E402

NEEDLE = "NEEDLE-TRACE-4417: KeyError: 'session_id'"
ERROR_TEXT = (
    "Traceback (most recent call last):\n"
    + "  File \"/app/auth.py\", line 212, in handler\n    user = lookup(session)\n" * 80
    + NEEDLE + "\n"
    + "INFO:worker:heartbeat ok\n" * 80
)
PROSE_TEXT = (
    "The dashboard renders all panels under two seconds. "
    "Backfill jobs finished without retries. ") * 200


def stub_needle_judge(chunks, task):
    return [0.95 if "NEEDLE" in c else 0.05 for c in chunks], 0.0


def stub_drop_all(chunks, task):
    return [0.01] * len(chunks), 0.0


def _msgs():
    return [
        {"role": "user", "content": "fix the auth test"},
        {"role": "tool", "content": ERROR_TEXT},
        {"role": "tool", "content": PROSE_TEXT},
    ]


def test_error_dense_detects_traceback():
    assert squeeze._is_error_dense(ERROR_TEXT)
    assert not squeeze._is_error_dense(PROSE_TEXT)
    assert not squeeze._is_error_dense("Traceback (most recent call last):\nshort")


def test_two_tier_beats_single_tier_with_recall():
    out1, s1 = squeeze.squeeze_transcript(_msgs(), "fix it", two_tier=False)
    out2, s2 = squeeze.squeeze_transcript(_msgs(), "fix it", two_tier=True)
    body = "\n".join(m["content"] for m in out2)
    assert NEEDLE in body, "needle lost"
    assert s2["chunks_total"] > s1["chunks_total"], "expected finer chunks"
    assert s2["reduction_pct"] >= s1["reduction_pct"], "no worse reduction"


def test_prose_keeps_large_chunks_in_both_modes():
    out1, _ = squeeze.squeeze_transcript(
        [{"role": "user", "content": "x"}, {"role": "tool", "content": PROSE_TEXT}],
        "t", two_tier=False)
    out2, _ = squeeze.squeeze_transcript(
        [{"role": "user", "content": "x"}, {"role": "tool", "content": PROSE_TEXT}],
        "t", two_tier=True)
    assert out1[1]["content"] == out2[1]["content"], "prose path must be identical"


def test_fail_safe_keeps_best_chunk_when_judge_drops_all():
    real = jev.score_chunks
    jev.score_chunks = stub_drop_all
    try:
        out, _ = squeeze.squeeze_transcript(_msgs(), "fix it", two_tier=True)
    finally:
        jev.score_chunks = real
    for m in out:
        if m["role"] == "tool":
            assert "kept 0/" not in m["content"], "tool result emptied"


def main():
    real = jev.score_chunks
    jev.score_chunks = stub_needle_judge
    try:
        for name, fn in sorted(
                [(k, v) for k, v in globals().items() if k.startswith("test_")]):
            fn()
            print(f"PASS {name}")
    finally:
        jev.score_chunks = real


if __name__ == "__main__":
    main()
