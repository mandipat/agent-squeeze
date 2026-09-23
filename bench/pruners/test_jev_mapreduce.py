"""Tests: --mapreduce mode of the v2 context-aware pruner.

Run: python3 test_jev_mapreduce.py   (from bench/pruners/)
The Jev judge is stubbed with a deterministic scorer — no Jev, no paid
calls. The sqlite decision cache points at a tmp file (the real
~/.agent_squeeze/decisions.sqlite is untouched).
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", ".."))
import jev_context_prune as v2  # noqa: E402
from agent_squeeze.context import parse  # noqa: E402


def make_doc():
    # 8 turns: user q; Read boilerplate (judge-drop); Bash ERROR
    # (fail-safe keep); long chatter (judge-drop); Grep with a planted
    # needle (judge-keep); chatter with needle in text (judge-keep);
    # 2 recent turns (auto-kept, PROTECT_RECENT).
    boi = ("heartbeat ok\nhealth check passed\n" * 200)
    messages = [
        {"role": "user", "content": "Where is the API key bug?"},
        {"role": "assistant", "content": "Reading the config.",
         "tool_calls": [{"id": "toolu_1", "name": "Read",
                         "arguments": {"file_path": "/tmp/a.py"}}]},
        {"role": "tool", "name": "Read", "tool_call_id": "toolu_1",
         "content": boi},
        {"role": "assistant", "content": "Running tests.",
         "tool_calls": [{"id": "toolu_2", "name": "Bash",
                         "arguments": {"command": "pytest -q"}}]},
        {"role": "tool", "name": "Bash", "tool_call_id": "toolu_2",
         "content": "FAILED tests/test_auth.py - AssertionError: "
                    "expected API key rotation\nTraceback (most recent call "
                    "last):\n  File \"test_auth.py\", line 9\n"
                    "AssertionError: expected API key rotation"},
        {"role": "assistant",
         "content": "Status update: " + "still investigating. " * 120},
        {"role": "assistant", "content": "Searching for the key.",
         "tool_calls": [{"id": "toolu_3", "name": "Grep",
                         "arguments": {"pattern": "needle"}}]},
        {"role": "tool", "name": "Grep", "tool_call_id": "toolu_3",
         "content": "found NEEDLE-8817: the rotation key in "
                    "config/secrets.yaml line 42"},
        {"role": "assistant",
         "content": "Key finding: NEEDLE-8817 " + "is the rotation key. "
                    * 100},
        {"role": "user", "content": "Ok, fix it and summarize."},
        {"role": "assistant", "content": "Fixing now, will summarize."},
    ]
    return {"id": "mapreduce-test", "question": "Where is the API key bug?",
            "evidence": [], "expected_answer_contains": [],
            "messages": messages}


CALLS = []  # (state, questions) per stubbed jev_batch call


def stub_jev_batch(state, questions):
    """Deterministic stand-in for Jev: keep units whose question text
    mentions a needle/error keyword, drop the rest."""
    CALLS.append((state, questions))
    answers = {}
    for qid, q in questions.items():
        instr = q.get("instructions", "")
        p = 0.9 if any(k in instr for k in
                       ("NEEDLE-8817", "AssertionError", "CRITICAL")) else 0.1
        answers[qid] = {"type": "noul", "noul": p}
    return answers, 0.0


def fresh_turns(doc):
    turns = parse(doc["messages"])
    for t in turns:  # per-run flags, as in main()
        t.text_dropped = False
        t.summarized = ""
        t.protected = False
    return turns


def run_mode(mode, doc, cache):
    turns = fresh_turns(doc)
    v2.jev_batch = stub_jev_batch
    if mode == "mapreduce":
        stats = v2.jev_pass_mapreduce(turns, doc["question"], cache)
    else:
        stats = v2.jev_pass(turns, doc["question"], cache)
    out = v2.reassemble(turns)
    return stats, out, turns


def tmp_cache():
    d = tempfile.mkdtemp()
    v2.CACHE_DB = os.path.join(d, "decisions.sqlite")
    return v2.DecisionCache()


def unit_state(turns):
    """(pair replacements, text_dropped) for decision-parity comparison."""
    pairs = {(p.id,): p.replacement for t in turns for p in t.tool_pairs}
    texts = {t.index: t.text_dropped for t in turns}
    return pairs, texts


def main():
    doc = make_doc()

    # --- Test 1: one Jev call for the whole transcript ---
    cache = tmp_cache()
    CALLS.clear()
    mstats, mout, mturns = run_mode("mapreduce", doc, cache)
    assert mstats["jev_calls"] == 1, \
        f"expected 1 Jev call, got {mstats['jev_calls']}"
    assert mstats["jev_questions"] == len(CALLS[0][1]), \
        "all uncached questions must ride the single call"
    state, questions = CALLS[0]
    assert "TRANSCRIPT MAP" in state, "map-reduce state needs the static map"

    # --- Test 2: sequential needs one call per unit (the latency pain) ---
    cache2 = tmp_cache()
    CALLS.clear()
    sstats, sout, sturns = run_mode("sequential", doc, cache2)
    assert sstats["jev_calls"] > 1, \
        f"expected sequential to need >1 call, got {sstats['jev_calls']}"
    assert sstats["jev_questions"] == mstats["jev_questions"], \
        "same unit question count in both modes"

    # --- Test 3: decision parity under identical judge answers ---
    assert unit_state(mturns) == unit_state(sturns), \
        "map-reduce must judge the same units the same way as sequential"

    # --- Test 4: fail-safes — unresolved error kept verbatim, never judged ---
    err_pair = next(p for t in mturns for p in t.tool_pairs if p.is_error)
    assert not err_pair.replacement, "unresolved error must stay verbatim"
    for st, qs in CALLS:
        for qid, q in qs.items():
            assert "AssertionError" not in q.get("instructions", ""), \
                "error output must never reach the judge"

    # --- Test 5: rerun is free — same decisions, zero calls ---
    CALLS.clear()
    mstats2, _, mturns2 = run_mode("mapreduce", doc, cache)
    assert mstats2["jev_calls"] == 0, \
        f"expected 0 calls on rerun, got {mstats2['jev_calls']}"
    assert mstats2["pairs_kept"] == mstats["pairs_kept"] and \
        mstats2["text_dropped"] == mstats["text_dropped"], \
        "cached rerun must reproduce the same decisions"

    # --- Test 6: same token reduction in both modes ---
    def chars(out):
        import json as _j
        return len(_j.dumps(out))
    before = len(__import__("json").dumps(doc["messages"]))
    mr = 100 * (before - chars(mout)) / before
    sq = 100 * (before - chars(sout)) / before
    assert mr == sq, f"token reduction diverged: {mr} vs {sq}"

    print(f"PASS: mapreduce 1 call/{mstats['jev_questions']}q vs "
          f"sequential {sstats['jev_calls']} calls/{sstats['jev_questions']}q; "
          f"parity ok, cache rerun 0 calls, reduction {mr:.1f}% both modes")


if __name__ == "__main__":
    main()
