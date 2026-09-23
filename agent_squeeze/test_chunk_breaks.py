"""Unit tests for chunk_text_breaks / reassemble_kept (Run 24) —
run: python3 test_chunk_breaks.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent_squeeze import jev  # noqa: E402
from agent_squeeze.squeeze import (  # noqa: E402
    chunk_text, chunk_text_breaks, reassemble_kept, squeeze_transcript,
)

SPLIT_NEEDLE = "SPLIT_NEEDLE_9ZQ4"
WHOLE_NEEDLE = "WHOLE_NEEDLE_2KX7"


def test_breaks_mark_hard_splits():
    line = "x" * 4400
    parts = chunk_text_breaks(line, 1500)
    assert len(parts) == 3, parts
    assert [p[1] for p in parts] == [True, True, False], parts
    # chunk texts byte-identical to the legacy chunk_text
    assert [p[0] for p in parts] == chunk_text(line, 1500)
    print("hard splits marked on long single line. OK")


def test_breaks_normal_lines_are_soft():
    text = "alpha\nbeta\ngamma\ndelta"
    parts = chunk_text_breaks(text, 1500)
    assert len(parts) == 1 and parts[0][1] is False
    assert parts[0][0] == text
    print("line-packed chunks have soft breaks. OK")


def test_reassemble_roundtrips_long_line():
    line = "y" * 4400
    parts = chunk_text_breaks(line, 1500)
    assert reassemble_kept(parts) == line
    print("long line roundtrips byte-identically. OK")


def test_reassemble_roundtrips_mixed():
    text = "header line\n" + "z" * 3200 + "\nfooter line"
    parts = chunk_text_breaks(text, 1500)
    assert reassemble_kept(parts) == text, "mixed hard/soft breaks roundtrip"
    print("mixed hard/soft breaks roundtrip. OK")


def _msgs(split_at):
    chars = ["x"] * 4400
    chars[200:200 + len(WHOLE_NEEDLE)] = list(WHOLE_NEEDLE)
    chars[split_at:split_at + len(SPLIT_NEEDLE)] = list(SPLIT_NEEDLE)
    tool_result = (
        "Traceback (most recent call last):\n"
        '  File "runner.py", line 88, in main\n'
        "    raise ValueError(\"payload too large\")\n"
        "ValueError: payload too large\n"
        + "".join(chars)
        + "\nFAILED tests/test_payload.py::test_large - ValueError\n"
    )
    return [
        {"role": "user", "content": "investigate the failing payload test"},
        {"role": "assistant", "content": "running the payload test now",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "run_test", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "run_test",
         "content": tool_result},
    ]


def _fragment_judge(chunks, task):
    # keep chunks containing any >=6-char fragment of the split needle —
    # a judge that reasons about pieces, unlike a verbatim-string judge
    frags = {SPLIT_NEEDLE.lower()[i:i + 6]
             for i in range(len(SPLIT_NEEDLE) - 5)}
    probs = []
    for c in chunks:
        cl = c.lower()
        hit = any(cl[j:j + 6] in frags for j in range(len(cl) - 5))
        probs.append(0.95 if hit else 0.05)
    return probs, 0.0


def test_split_needle_survives_verbatim_when_fragments_kept():
    # needle straddles the 1500-char hard-split boundary; the two fragments
    # must reassemble to the verbatim needle (no injected "\\n")
    old = jev.score_chunks
    jev.score_chunks = _fragment_judge
    try:
        new_msgs, stats = squeeze_transcript(_msgs(1495), "find failures")
    finally:
        jev.score_chunks = old
    tool = [m for m in new_msgs if m.get("role") == "tool"][0]
    assert SPLIT_NEEDLE in tool["content"], \
        "split needle reassembled verbatim: %r" % tool["content"][:200]
    assert WHOLE_NEEDLE in tool["content"]
    print("split needle reassembles verbatim under two-tier. OK")


def test_all_kept_is_byte_identical():
    old = jev.score_chunks
    jev.score_chunks = lambda chunks, task: ([1.0] * len(chunks), 0.0)
    try:
        msgs = _msgs(1000)
        new_msgs, _ = squeeze_transcript(msgs, "find failures")
    finally:
        jev.score_chunks = old
    before = [m for m in msgs if m.get("role") == "tool"][0]["content"]
    after = [m for m in new_msgs if m.get("role") == "tool"][0]["content"]
    # chunk_text has always dropped a trailing newline (split("\n") yields a
    # final empty line that is skipped) — the claim is everything else
    # roundtrips exactly, with no "\n" injected at hard splits.
    assert after == before.rstrip("\n"), \
        "keep-everything must roundtrip the tool result (modulo the " \
        "pre-existing trailing-newline drop)"
    print("keep-everything roundtrips the tool result byte-identically. OK")


if __name__ == "__main__":
    test_breaks_mark_hard_splits()
    test_breaks_normal_lines_are_soft()
    test_reassemble_roundtrips_long_line()
    test_reassemble_roundtrips_mixed()
    test_split_needle_survives_verbatim_when_fragments_kept()
    test_all_kept_is_byte_identical()
    print("ALL CHUNK-BREAK TESTS PASSED")
