"""Unit tests for chunk_text_breaks_overlap / overlap reassembly (Run 25) —
run: python3 test_chunk_overlap.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent_squeeze import jev  # noqa: E402
from agent_squeeze.cache import squeeze_with_policy  # noqa: E402
from agent_squeeze.squeeze import (  # noqa: E402
    OVERLAP_CHARS,
    _kept_parts,
    chunk_text_breaks,
    chunk_text_breaks_overlap,
    reassemble_kept,
    squeeze_transcript,
)

SPLIT_NEEDLE = "SPLIT_NEEDLE_9ZQ4"


def _long_line_fixture(split_at=1495):
    line = "x" * 4400
    line = (line[:split_at] + SPLIT_NEEDLE +
            line[split_at + len(SPLIT_NEEDLE):])
    tool_result = (
        "Traceback (most recent call last):\n"
        '  File "runner.py", line 88, in main\n'
        "    raise ValueError(\"payload too large\")\n"
        "ValueError: payload too large\n"
        "--- tool output (minified JSON, one line) ---\n"
        + line +
        "\nFAILED tests/test_payload.py::test_large - ValueError\n"
    )
    return [
        {"role": "user", "content": "investigate the failing payload test"},
        {"role": "tool", "content": tool_result},
    ]


def _perfect_judge(chunks, task):
    needle = SPLIT_NEEDLE.lower()
    return ([0.95 if needle in c.lower() else 0.05 for c in chunks], 0.0)


def test_overlap_only_after_hard_splits():
    line = "x" * 4400
    parts = chunk_text_breaks_overlap(line, 1500, 100)
    assert len(parts) == 3, parts
    assert [p[1] for p in parts] == [True, True, False]
    assert [p[2] for p in parts] == [100, 100, 0], parts
    # chunk 0 = core + first 100 chars of chunk 1's core
    assert parts[0][0] == line[0:1500] + line[1500:1600]
    assert parts[1][0] == line[1500:3000] + line[3000:3100]
    assert parts[2][0] == line[3000:4400]
    print("overlap appended only after hard splits. OK")


def test_overlap_zero_on_packed_lines():
    text = "alpha\nbeta\ngamma"
    parts = chunk_text_breaks_overlap(text, 1500, 100)
    assert len(parts) == 1
    assert parts[0] == (text, False, 0), parts
    # and with overlap_chars=0, texts match the legacy chunker exactly
    legacy = [c for c, _ in chunk_text_breaks(text, 1500)]
    mine = [c for c, _, _ in chunk_text_breaks_overlap(text, 1500, 0)]
    assert mine == legacy
    print("packed lines get no overlap; overlap_chars=0 == legacy. OK")


def test_reassemble_strips_when_successor_kept():
    line = "x" * 4400
    parts = chunk_text_breaks_overlap(line, 1500, 100)
    chunks = [(0, c, h, ov) for c, h, ov in parts]
    kept = _kept_parts(chunks, {0, 1, 2}, [0])
    out = reassemble_kept(kept[0])
    assert out == line, "keep-all must roundtrip byte-identical"
    print("keep-all roundtrips byte-identical (overlap stripped). OK")


def test_reassemble_keeps_overlap_when_successor_dropped():
    line = "x" * 4400
    parts = chunk_text_breaks_overlap(line, 1500, 100)
    chunks = [(0, c, h, ov) for c, h, ov in parts]
    # keep chunk 0 and chunk 2, drop the middle: chunk 0's successor is
    # dropped, so its overlap stays (judged content with no other home);
    # chunk 2 has no overlap. No bytes duplicated, no gap misjoined.
    kept = _kept_parts(chunks, {0, 2}, [0])
    out = reassemble_kept(kept[0])
    assert out == line[0:1600] + line[3000:], (
        "expected core0+overlap then core2, got %d chars" % len(out))
    assert out.count("x" * 100) >= 1
    print("dropped successor -> overlap kept, no duplication. OK")


def test_reassemble_kept_still_accepts_legacy_tuples():
    # backwards compat: 2-tuples from chunk_text_breaks keep working
    line = "x" * 4400
    parts = [(c, h) for c, h in chunk_text_breaks(line, 1500)]
    assert reassemble_kept(parts) == line
    print("reassemble_kept still accepts legacy 2-tuples. OK")


def test_squeeze_overlap_rescues_straddling_needle():
    old = jev.score_chunks
    jev.score_chunks = _perfect_judge
    try:
        msgs = _long_line_fixture(1495)
        new0, _ = squeeze_transcript(msgs, "find failures", two_tier=True,
                                    overlap_chars=0)
        blob0 = new0[1]["content"]
        new1, s1 = squeeze_transcript(msgs, "find failures", two_tier=True,
                                     overlap_chars=OVERLAP_CHARS)
        blob1 = new1[1]["content"]
    finally:
        jev.score_chunks = old
    assert SPLIT_NEEDLE not in blob0, \
        "without overlap the fragment-blind judge must still lose it"
    assert SPLIT_NEEDLE in blob1, \
        "with overlap the fragment-blind judge must see it whole"
    assert s1["chunks_total"] == 4, s1  # same judge-call count
    print("squeeze_transcript overlap rescues the straddling needle. OK")


def test_cache_path_overlap_rescues_too():
    msgs = _long_line_fixture(1495)
    new0, _ = squeeze_with_policy(msgs, "find failures", _perfect_judge,
                                  overlap_chars=0)
    new1, _ = squeeze_with_policy(msgs, "find failures", _perfect_judge,
                                 overlap_chars=OVERLAP_CHARS)
    assert SPLIT_NEEDLE not in new0[1]["content"]
    assert SPLIT_NEEDLE in new1[1]["content"]
    print("cache.squeeze_with_policy overlap rescues the needle too. OK")


def test_overlap_off_is_byte_identical_to_legacy():
    old = jev.score_chunks
    jev.score_chunks = _perfect_judge
    try:
        msgs = _long_line_fixture(1000)  # needle inside a chunk
        new_legacy, _ = squeeze_transcript(msgs, "find failures",
                                          two_tier=True)
        new_ov0, _ = squeeze_transcript(msgs, "find failures", two_tier=True,
                                       overlap_chars=0)
    finally:
        jev.score_chunks = old
    assert new_legacy[1]["content"] == new_ov0[1]["content"]
    print("overlap_chars=0 path is byte-identical to the legacy call. OK")


if __name__ == "__main__":
    test_overlap_only_after_hard_splits()
    test_overlap_zero_on_packed_lines()
    test_reassemble_strips_when_successor_kept()
    test_reassemble_keeps_overlap_when_successor_dropped()
    test_reassemble_kept_still_accepts_legacy_tuples()
    test_squeeze_overlap_rescues_straddling_needle()
    test_cache_path_overlap_rescues_too()
    test_overlap_off_is_byte_identical_to_legacy()
    print("ALL CHUNK-OVERLAP TESTS PASSED")
