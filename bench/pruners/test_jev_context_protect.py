"""Tests: --protect-prefix for the v2 context-aware pruner.

Run: python3 test_jev_context_protect.py   (from bench/pruners/)
The Jev judge is stubbed (always drops, records what it was asked about) —
no Jev, no paid calls, decision cache unused.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", ".."))
import jev_context_prune as v2  # noqa: E402
from agent_squeeze.context import parse  # noqa: E402

PROTMARKER = "PROTMARKER-9f3a2"


def make_doc():
    # Turn 0: user question (sacred anyway). Turn 1: protected read of
    # /tmp/app.py with the marker in its result. Turn 2: re-read of the
    # same file — without protection, pass 0 would mark turn 1's read
    # SUPERSEDED. Turn 3: long assistant chatter judged (dropped) in the
    # tail.
    messages = [
        {"role": "user",
         "content": "What is the refactor plan?"},
        {"role": "assistant", "content": "Reading the file.",
         "tool_calls": [{"id": "toolu_1", "name": "Read",
                         "arguments": {"file_path": "/tmp/app.py"}}]},
        {"role": "tool", "name": "Read", "tool_call_id": "toolu_1",
         "content": f"PROTMARKER line of code\nprint('hello')\n"},
        {"role": "assistant", "content": "Reading again.",
         "tool_calls": [{"id": "toolu_2", "name": "Read",
                         "arguments": {"file_path": "/tmp/app.py"}}]},
        {"role": "tool", "name": "Read", "tool_call_id": "toolu_2",
         "content": "PROTMARKER line of code\nprint('hello')\n"},
        {"role": "assistant",
         "content": "Status chatter: " + "everything is proceeding fine. " * 60},
        {"role": "user", "content": "Ok, summarize next steps."},
        {"role": "assistant", "content": "Here are the next steps."},
        {"role": "user", "content": "Thanks, carry on."},
    ]
    return {"id": "protect-test", "question": "What is the refactor plan?",
            "evidence": [], "expected_answer_contains": [],
            "messages": messages}


JUDGED_MARKERS = []


def stub_jev_batch(state, questions):
    # Assert the judge was never handed protected content as the *subject*.
    # (Protected text legitimately appears in `state` as ledger context.)
    for qid, q in questions.items():
        instr = q.get("instructions", "")
        assert PROTMARKER not in instr, \
            f"judge asked about protected content in {qid}"
    return ({qid: {"noul": 0.0} for qid in questions}, 0.0)


def run_pipeline(protect_chars):
    doc = make_doc()
    turns = parse(doc["messages"])
    for t in turns:
        t.text_dropped = False
        t.summarized = ""
        t.protected = False
    n_protected, protected_chars = v2.mark_protected(turns, protect_chars)
    real = v2.jev_batch
    real_db = v2.CACHE_DB
    tmpdb = os.path.join(tempfile.mkdtemp(), "decisions.sqlite")
    v2.jev_batch = stub_jev_batch
    v2.CACHE_DB = tmpdb
    try:
        n_replaced, _ = v2.deterministic_pass(turns)
        jstats = v2.jev_pass(turns, doc["question"], v2.DecisionCache())
    finally:
        v2.jev_batch = real
        v2.CACHE_DB = real_db
    messages_after = v2.reassemble(turns)
    return turns, doc, messages_after, n_protected, n_replaced, jstats


def test_protected_turns_byte_identical():
    protect = sum(len(json.dumps(m)) for m in make_doc()["messages"][:3])
    turns, doc, after, n_protected, n_replaced, jstats = \
        run_pipeline(protect)
    assert n_protected == 2, f"expected 2 protected turns, got {n_protected}"
    assert after[:3] == doc["messages"][:3], \
        "protected prefix not byte-identical in output"
    # pass 0 must not have marked the protected read as SUPERSEDED
    t1 = turns[1]
    assert len(t1.tool_pairs) == 1
    assert t1.tool_pairs[0].replacement == "", \
        f"pass 0 touched protected turn: {t1.tool_pairs[0].replacement!r}"
    assert jstats["pairs_protected"] == 1
    assert jstats["text_protected"] == 1
    print("test_protected_turns_byte_identical: PASS")


def test_tail_still_judged_and_prefix_never_in_cache_or_judge():
    protect = sum(len(json.dumps(m)) for m in make_doc()["messages"][:3])
    turns, doc, after, n_protected, n_replaced, jstats = \
        run_pipeline(protect)
    # the stub judge dropped everything it saw; the long tail chatter turn
    # must have been dropped (or judged), i.e. the tail was really judged
    tail_turn = turns[3]  # parser merges tool msgs into assistant turns
    assert tail_turn.text_dropped, \
        "tail chatter turn was never judged"
    assert jstats["jev_calls"] >= 1, "no Jev calls happened at all"
    # and per the stub's own assertion, the marker never appeared in a
    # judging question — the protected prefix was invisible to the judge
    print("test_tail_still_judged_and_prefix_never_in_cache_or_judge: PASS")


def test_default_path_unchanged():
    # protect=0: classic behavior — the earlier read IS marked superseded.
    turns, doc, after, n_protected, n_replaced, jstats = run_pipeline(0)
    assert n_protected == 0
    t1 = turns[1]
    assert "SUPERSEDED" in t1.tool_pairs[0].replacement, \
        f"classic path should mark the first read superseded, got " \
        f"{t1.tool_pairs[0].replacement!r}"
    assert jstats["pairs_protected"] == 0
    assert jstats["text_protected"] == 0
    print("test_default_path_unchanged: PASS")


def test_never_splits_a_turn():
    doc = make_doc()
    turns = parse(doc["messages"])
    for t in turns:
        t.protected = False
    first_chars = sum(len(json.dumps(m)) for m in doc["messages"][:3])
    # one char short of covering turn 1 (msgs 1+2 merged) -> only turn 0
    # protected; the turn is never partially protected
    n, _ = v2.mark_protected(turns, first_chars - 1)
    assert n == 1, f"turn split at boundary: {n}"
    protected_flags = [t.protected for t in turns]
    assert protected_flags == \
        [True, False, False, False, False, False, False], \
        protected_flags
    print("test_never_splits_a_turn: PASS")


if __name__ == "__main__":
    test_protected_turns_byte_identical()
    test_tail_still_judged_and_prefix_never_in_cache_or_judge()
    test_default_path_unchanged()
    test_never_splits_a_turn()
    print("ALL PROTECT TESTS PASS")
