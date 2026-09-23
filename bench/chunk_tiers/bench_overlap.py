"""Overlap-window experiment (Run 25): does a 100-char overlap on hard splits
close Run 24's boundary-split recall regression for fragment-blind judges?

Run 24 found: a judge that keeps a chunk only when it contains the needle
verbatim drops BOTH halves of a needle straddling two-tier's 1500-char
hard-split boundary — a recall regression vs single-tier that no chunker
avoids. New in this run: chunk_text_breaks_overlap (squeeze.py) appends
the first 100 chars of the next chunk to every hard-split chunk, so the
fragment-blind judge sees any needle within 100 chars of the boundary whole
in the earlier chunk. Reassembly strips the overlap, so output bytes are
unaffected.

Method: deterministic, reuses Run 24's fixture (4400-char single-line
minified-JSON payload, needle SPLIT_NEEDLE_9ZQ4 placed at several offsets
around the 1500 boundary, WHOLE_NEEDLE_2KX7 control inside a chunk).
Needle length 16: it straddles the 1500 boundary iff start in (1484, 1500).

Judges (deterministic, no Jev, no OpenRouter):
  - perfect: keep iff chunk contains the FULL needle verbatim (fragment-blind)
  - fragment-aware: keep iff chunk contains any >=6-char fragment

Run: cd bench/chunk_tiers && python3 bench_overlap.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench_boundary_split as B  # noqa: E402
from agent_squeeze import jev  # noqa: E402
from agent_squeeze.squeeze import (  # noqa: E402
    OVERLAP_CHARS, chunk_text_breaks, chunk_text_breaks_overlap, squeeze_transcript,
)

OFFSETS = {
    "inside_chunk0": 1470,    # 1470..1486, whole in chunk 0 — control
    "straddle_1485": 1485,    # straddles 1500 (1485..1501)
    "straddle_1495": 1495,    # straddles 1500 (1495..1511)
    "straddle_1499": 1499,    # straddles 1500 (1499..1515)
    "at_boundary": 1500,      # 1500..1516, whole in chunk 1 — control
    "inside_chunk1": 1520,    # whole in chunk 1 — control
}


def run_case(split_at, fragment_aware, overlap):
    msgs = B.build_messages(split_at)
    jev.score_chunks = B._judge_factory(B.SPLIT_NEEDLE, fragment_aware)
    new_msgs, stats = squeeze_transcript(msgs, "find failures",
                                        two_tier=True, overlap_chars=overlap)
    rec = B.recall(new_msgs)
    return rec[B.SPLIT_NEEDLE], rec[B.WHOLE_NEEDLE], stats


def overhead():
    """Extra judge-input chars the overlap adds on the fixture."""
    msgs = B.build_messages(1495)
    content = msgs[2]["content"]
    plain = sum(len(c) for c, _ in chunk_text_breaks(content, 1500))
    ov = sum(len(c) for c, _, _ in
             chunk_text_breaks_overlap(content, 1500, OVERLAP_CHARS))
    return plain, ov, 100.0 * (ov - plain) / max(1, plain)


def roundtrip_check():
    """Keep-all through the overlap path must reproduce the input bytes
    (overlap stripped on reassembly, no duplication)."""
    msgs = B.build_messages(1495)
    jev.score_chunks = lambda chunks, task: ([0.99] * len(chunks), 0.0)
    new_msgs, _ = squeeze_transcript(msgs, "find failures",
                                    two_tier=True,
                                    overlap_chars=OVERLAP_CHARS)
    orig = msgs[2]["content"]
    got = new_msgs[2]["content"]
    return got == orig or got == orig.rstrip("\n"), orig, got


def main():
    print("== recall: perfect (fragment-blind) judge, two-tier ==")
    print("%-14s %-10s %-10s" % ("offset", "ov=0", "ov=100"))
    all_ok = True
    for name, off in OFFSETS.items():
        r0, _, _ = run_case(off, False, 0)
        r1, _, _ = run_case(off, False, OVERLAP_CHARS)
        mark = "" if r1 else "  <-- LOST"
        if not r1:
            all_ok = False
        print("%-14s %-10s %-10s%s" % (name, r0, r1, mark))
    print("\noverlap=100 rescues every straddling needle for the "
          "fragment-blind judge:", all_ok)

    print("\n== recall: fragment-aware judge, two-tier (sanity) ==")
    for name, off in OFFSETS.items():
        r0, w0, _ = run_case(off, True, 0)
        r1, w1, _ = run_case(off, True, OVERLAP_CHARS)
        assert w0 and w1, (name, "control needle lost")
        print("%-14s ov=0 split=%s whole=%s | ov=100 split=%s whole=%s"
              % (name, r0, w0, r1, w1))

    plain, ov, pct = overhead()
    print("\n== judge-input overhead (fixture, 4400-char long line) ==")
    print("plain chunk chars: %d | overlap chunk chars: %d | +%d chars "
          "(+%.1f%%); judge calls unchanged" % (plain, ov, ov - plain, pct))

    ok, orig, got = roundtrip_check()
    print("\n== keep-all roundtrip through overlap path ==")
    print("byte-identical (mod trailing newline):", ok,
          "(%d -> %d chars)" % (len(orig), len(got)))

    # the regression question, stated plainly
    reg_before = any(not run_case(off, False, 0)[0]
                     for off in (1485, 1495, 1499))
    reg_after = any(not run_case(off, False, OVERLAP_CHARS)[0]
                    for off in (1485, 1495, 1499))
    print("\nfragment-blind judge loses a straddling needle: "
          "overlap=0 -> %s | overlap=100 -> %s" % (reg_before, reg_after))


if __name__ == "__main__":
    main()
