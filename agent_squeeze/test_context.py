"""Unit tests for agent_squeeze.context — run: python3 test_context.py"""
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from context import (  # noqa: E402
    parse, task_intent, iter_tool_units, pair_target, turn_text_for_judge,
)

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.join(HERE, "..", "bench")


def load_converter():
    path = os.path.join(BENCH, "real_sessions", "to_bench_input.py")
    spec = importlib.util.spec_from_file_location("to_bench_input", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_task1_transcript():
    """Real Claude Code stream-json transcript -> bench input -> turns."""
    conv = load_converter()
    tpath = os.path.join(BENCH, "real_sessions", "task1", "transcript.jsonl")
    events = [json.loads(l) for l in open(tpath) if l.strip()]
    messages = conv.convert(events)
    assert messages, "converter produced no messages"

    turns = parse(messages)
    assert turns, "no turns parsed"
    # NOTE: task1's transcript holds no user text at all (the -p prompt is
    # not recorded; all user events are tool_result blocks). The task intent
    # for such transcripts comes from the bench doc's "question" field.
    assert task_intent(turns) == "", "expected no user turn in task1"
    roles = {t.role for t in turns}
    assert roles <= {"user", "assistant"}, f"unexpected roles {roles}"

    # every tool message's tool_call_id must pair with a tool_use id
    tool_ids = {m.get("tool_call_id") for m in messages
                if m.get("role") == "tool"}
    paired_ids = {p.id for p in iter_tool_units(turns)}
    assert tool_ids <= paired_ids, \
        f"unpaired tool results: {tool_ids - paired_ids}"

    # tool_use + tool_result live in the same turn (atomic unit)
    for p in iter_tool_units(turns):
        if p.has_result:
            assert p.use_turn == p.result_turn, \
                f"pair {p.id} split across turns"

    # task intent comes from the first user message (synthetic bench test
    # below covers this); for transcripts without one the pruner falls back
    # to the bench doc's "question" field.
    intent = task_intent(turns)
    assert intent == "", "task1 has no user text; intent must be empty"

    # every original message position is accounted for exactly once
    positions = [pos for t in turns for pos, _ in t.source]
    assert sorted(positions) == list(range(len(messages))), \
        "message positions not faithfully covered"

    n_pairs = sum(1 for _ in iter_tool_units(turns))
    print(f"task1: {len(messages)} messages -> {len(turns)} turns, "
          f"{n_pairs} tool pairs. OK")


def test_messages_api_synthetic():
    """True Messages API shape: blocks, id pairing, thinking dropped."""
    messages = [
        {"role": "user", "content": "read foo.py and tell me what it does"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "internal reasoning here"},
            {"type": "text", "text": "I'll read it."},
            {"type": "tool_use", "id": "toolu_1", "name": "Read",
             "input": {"file_path": "foo.py"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1",
             "content": [{"type": "text", "text": "print('hi')"}],
             "is_error": False},
        ]},
        {"role": "assistant", "content": "It prints hi."},
        # string content + orphan error result
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_9",
             "content": "boom", "is_error": True},
        ]},
    ]
    turns = parse(messages, fmt="messages_api")
    assert len(turns) == 5, f"expected 5 turns, got {len(turns)}"
    assert task_intent(turns) == "read foo.py and tell me what it does"
    pairs = list(iter_tool_units(turns))
    assert len(pairs) == 2, f"expected 2 pairs, got {len(pairs)}"
    p1 = next(p for p in pairs if p.id == "toolu_1")
    assert p1.name == "Read" and p1.result == "print('hi')"
    assert p1.use_turn == 1 and p1.result_turn == 2
    assert pair_target(p1) == "foo.py"
    assert not p1.is_error
    p9 = next(p for p in pairs if p.id == "toolu_9")
    assert p9.is_error and p9.result == "boom"
    # thinking block parsed but excluded from judge text
    t1 = turns[1]
    assert any(b["type"] == "thinking" for b in t1.blocks)
    assert "internal reasoning" not in turn_text_for_judge(t1)
    assert "I'll read it." in t1.text
    print("messages_api synthetic: pairing, thinking-drop, orphans. OK")


def test_bench_format_synthetic():
    """Bench input shape: assistant tool_calls + role=tool messages merge."""
    messages = [
        {"role": "user", "content": "fix the bug"},
        {"role": "assistant", "content": "On it.",
         "tool_calls": [{"id": "t1", "name": "Bash",
                         "arguments": {"command": "pytest -x"}}]},
        {"role": "tool", "name": "Bash", "tool_call_id": "t1",
         "content": "Traceback (most recent call last): ... AssertionError"},
    ]
    turns = parse(messages, fmt="bench")
    assert len(turns) == 2, f"expected 2 turns, got {len(turns)}"
    assert len(turns[1].tool_pairs) == 1
    p = turns[1].tool_pairs[0]
    assert p.id == "t1" and p.use_turn == p.result_turn == 1
    assert p.is_error, "traceback should be detected as error"
    assert "pytest -x" in pair_target(p)
    print("bench synthetic: merge + error re-detection. OK")


if __name__ == "__main__":
    test_task1_transcript()
    test_messages_api_synthetic()
    test_bench_format_synthetic()
    print("ALL CONTEXT TESTS PASSED")
