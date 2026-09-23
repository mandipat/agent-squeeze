"""Tests: policy_fn is honored on the classic (non-protect) squeeze paths.

Run 32 found the gap: fleet.squeeze_fleet silently ignored policy_fn on the
non-protect path (protect_tokens == 0) because squeeze_transcript had no
policy_fn parameter — any injected free/test policy fell through to the
PAID jev.score_chunks. This file locks the fix.

Run: python3 test_policy_gap.py   (from agent_squeeze/)
Zero paid calls — jev.score_chunks is patched to raise in every test.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent_squeeze import jev as jev_mod  # noqa: E402
from agent_squeeze import squeeze  # noqa: E402
from agent_squeeze.fleet import squeeze_fleet  # noqa: E402


def make_stub():
    calls = []

    def stub_policy(chunks, task):
        calls.append(chunks)
        # Keep chunks mentioning "keepme", drop everything else (free).
        return ([1.0 if "keepme" in c else 0.0 for c in chunks], 0.0)

    stub_policy.calls = calls
    return stub_policy


def _raising(_chunks, _task):
    raise AssertionError("jev.score_chunks must not be called with an "
                         "injected policy")


def _patch_jev_raises():
    real = jev_mod.score_chunks
    jev_mod.score_chunks = _raising
    return real


def _restore_jev(real):
    jev_mod.score_chunks = real


def _msgs():
    # 600 boilerplate lines ≈ 12.6k chars ≈ 3 chunks at 6000 chars, so the
    # stub policy drops 2 chunks and the fail-safe keeps exactly 1.
    return [
        {"role": "user", "content": "migrate auth to JWT"},
        {"role": "assistant", "content": "reading config"},
        {"role": "tool", "content": "\n".join(
            f"boilerplate line {i}" for i in range(600))},
        {"role": "tool", "content": "keepme: deploy error traceback E501"},
    ]


def _text(out):
    return " ".join(m.get("content", "") for m in out)


def test_transcript_honors_injected_policy():
    real = _patch_jev_raises()
    stub = make_stub()
    try:
        out, stats = squeeze.squeeze_transcript(
            _msgs(), "migrate auth to JWT", policy_fn=stub)
    finally:
        _restore_jev(real)
    assert stub.calls, "the injected policy must be consulted"
    assert stats["chunks_total"] == 4
    # keepme chunk + fail-safe keeps exactly one boilerplate chunk
    assert stats["chunks_kept"] == 2
    text = _text(out)
    assert "keepme" in text, "injected policy's kept chunk must survive"
    assert "boilerplate line 500" not in text, \
        "injected policy's dropped chunks must go"
    assert "boilerplate line 100" in text, \
        "fail-safe (never empty a tool result) still applies"
    assert stats["cost_usd"] == 0.0, "injected policy reports its own cost"


def test_transcript_default_still_routes_to_jev():
    real = _patch_jev_raises()
    try:
        raised = False
        try:
            squeeze.squeeze_transcript(_msgs(), "migrate auth to JWT")
        except AssertionError:
            raised = True
    finally:
        _restore_jev(real)
    assert raised, "without policy_fn the default path must still reach Jev"


def test_fleet_nonprotect_honors_injected_policy():
    # The exact Run-32 gap: protect_tokens == 0 + injected policy used to
    # silently hit the paid Jev endpoint.
    real = _patch_jev_raises()
    stub = make_stub()
    try:
        t = {"a": _msgs(), "b": [
            {"role": "user", "content": "add rate limiting"},
            {"role": "assistant", "content": "reading config"},
            {"role": "tool", "content": "\n".join(
                f"boilerplate line {i}" for i in range(600))},  # exact dupe
            {"role": "tool", "content": "keepme: healthcheck nominal"},
        ]}
        out, report = squeeze_fleet(t, policy_fn=stub)
    finally:
        _restore_jev(real)
    assert stub.calls, "the injected policy must reach pass 2"
    a_text, b_text = _text(out["a"]), _text(out["b"])
    assert "keepme" in a_text, "policy decision must reach agent a's output"
    assert "boilerplate line 500" not in a_text, \
        "policy drops must apply on the classic pass-2 path"
    assert "shared context" in b_text, \
        "pass-1 exact dedup must still work"
    assert report["total_cost_usd"] == 0.0


def test_fleet_nonprotect_no_policy_still_jev():
    real = _patch_jev_raises()
    try:
        raised = False
        try:
            squeeze_fleet({"a": _msgs()})
        except AssertionError:
            raised = True
    finally:
        _restore_jev(real)
    assert raised, "fleet classic path without policy_fn must still reach Jev"
