"""Unit tests for agent_squeeze.cache — run: python3 test_cache.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent_squeeze.cache import (  # noqa: E402
    split_protected, squeeze_with_policy, squeeze_cache_aware,
    stable_prefix_tokens, cache_breakpoints, inject_cache_control,
    next_turn_cost_model, session_cost_model,
)


def policy(chunks, task):
    # drop chunks containing the marker word, keep the rest
    return ([0.0 if "DROPPABLE" in c else 1.0 for c in chunks], 0.0)


def mk_msgs():
    return [
        {"role": "system", "name": "", "content": "system prompt here"},
        {"role": "user", "name": "", "content": "do the thing"},
        {"role": "assistant", "name": "", "content": "[tool call: Bash]"},
        {"role": "tool", "name": "Bash",
         "content": "DROPPABLE heartbeat ok\n" * 200},          # ~5k chars
        {"role": "assistant", "name": "", "content": "[tool call: Read]"},
        {"role": "tool", "name": "Read",
         "content": "DROPPABLE heartbeat ok\n" * 200},
        {"role": "assistant", "name": "", "content": "Traceback shows X"},
        {"role": "tool", "name": "Bash",
         "content": "Traceback (most recent call last): ... AssertionError"},
        {"role": "user", "name": "", "content": "summarize"},
    ]


def test_split_never_splits_message():
    msgs = mk_msgs()
    pre, tail = split_protected(msgs, 200)
    assert len(pre) + len(tail) == len(msgs)
    assert pre + tail == msgs, "split must be a clean whole-message cut"
    assert all(m["content"] for m in pre)
    print("split_protected: whole-message cut. OK")


def test_prefix_byte_identical():
    msgs = mk_msgs()
    out, stats = squeeze_cache_aware(msgs, "t", protect_tokens=200,
                                     policy_fn=policy)
    pre, _ = split_protected(msgs, 200)
    for a, b in zip(pre, out):
        assert a["content"] == b["content"], "prefix must be byte-identical"
    assert stats["protected_tokens"] > 0
    assert stats["reduction_pct"] > 0, "tail noise should be pruned"
    # the error traceback in the tail must survive
    assert any("AssertionError" in m.get("content", "") for m in out)
    print("squeeze_cache_aware: prefix identical, tail pruned. OK")


def test_fail_safe_keeps_best_chunk():
    msgs = [{"role": "tool", "name": "Bash",
             "content": "DROPPABLE only\n" * 200}]
    out, _ = squeeze_with_policy(msgs, "t", policy)
    assert out[0]["content"].strip(), "tool result must never be emptied"
    print("squeeze_with_policy: fail-safe. OK")


def test_stable_prefix_counts_leading_only():
    a = [{"content": "same"}, {"content": "same"}, {"content": "x"},
         {"content": "same"}]
    b = [{"content": "same"}, {"content": "same"}, {"content": "y"},
         {"content": "same"}]
    assert stable_prefix_tokens(a, b) == 2, "must stop at first difference"
    print("stable_prefix_tokens: leading-only. OK")


def test_breakpoints_and_injection():
    msgs = mk_msgs()
    bps = cache_breakpoints(msgs, 200)
    assert bps and bps[-1] == len(msgs) - 1, "last message always a breakpoint"
    assert len(bps) <= 2
    anth = [{"role": m["role"],
             "content": [{"type": "text", "text": m["content"]}]}
            for m in msgs]
    injected = inject_cache_control(anth, bps)
    for i in bps:
        assert injected[i]["content"][0].get("cache_control") == \
            {"type": "ephemeral"}, f"breakpoint {i} missing cache_control"
    non_bp = [i for i in range(len(msgs)) if i not in bps]
    assert all("cache_control" not in b
               for i in non_bp for b in injected[i]["content"])
    assert all("cache_control" not in b
               for m in anth for b in m["content"]), "input must not mutate"
    print("cache_breakpoints + inject_cache_control. OK")


def test_cost_models():
    msgs = mk_msgs()
    out, _ = squeeze_cache_aware(msgs, "t", protect_tokens=200,
                                 policy_fn=policy)
    nxt = next_turn_cost_model(out, msgs, 3.0)
    assert nxt["stable_tokens"] > 0
    assert nxt["cost_next_aware_usd"] <= nxt["cost_next_naive_usd"]
    sess = session_cost_model(out, msgs, 3.0, turns=10)
    assert sess["session_aware_usd"] <= sess["session_naive_usd"]
    print("cost models: aware <= naive. OK")


if __name__ == "__main__":
    test_split_never_splits_message()
    test_prefix_byte_identical()
    test_fail_safe_keeps_best_chunk()
    test_stable_prefix_counts_leading_only()
    test_breakpoints_and_injection()
    test_cost_models()
    print("ALL CACHE TESTS PASSED")
