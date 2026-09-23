"""Two-tier chunk-boundary needle-split test (Run 24).

Open question from Runs 20-23: Run 21's regression bench used a perfect
evidence judge (keep chunk iff it contains an evidence string verbatim) and
showed 100% recall in both tiers on all 10 fixtures. But chunk_text() packs
LINES — boundaries fall on newlines, so needles (single-line strings) were
never split in those fixtures, and the split risk was never actually tested.

The one place a chunk boundary CAN land mid-string: a single line longer
than the chunk size (e.g. minified JSON tool output) gets hard-split at
max_chars (squeeze.py chunk_text). Two-tier chunks error-dense results at
1500 chars, so a line of 1500-6000 chars gets split by two-tier but stays
whole under single-tier (6000). If a needle straddles that split, a judge
that only keeps chunks containing the FULL needle drops both halves — a
real recall regression unique to two-tier, invisible to Run 21's bench.

Method: deterministic. Synthetic error-dense tool result (~4.5k chars):
a 4400-char single-line minified JSON payload with:
  - SPLIT_NEEDLE_9ZQ4 starting at char ~1495 of the line (straddles the
    1500-char hard-split boundary under two-tier; whole under single-tier)
  - WHOLE_NEEDLE_2KX7 at char ~200 (whole in both modes — control)
Plus a traceback header (error-dense trigger) and pytest failure footer.

Judges (both deterministic, no Jev, no OpenRouter):
  - perfect: keep iff the chunk contains the FULL needle string verbatim.
    Under two-tier this simulates a judge that cannot recognize a needle
    it can only see half of.
  - fragment-aware: keep iff the chunk contains any >=6-char fragment of
    the needle. This simulates a judge that reasons about pieces.

Honest framing: neither judge IS TypeSafe Jev. The experiment isolates the
chunking mechanism: does the finer split make the needle unjudgeable to a
fragment-blind judge? If yes, that is a real risk factor for real judges
on long-line payloads; if no, the boundary-split open item closes.

Run: python bench_boundary_split.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agent_squeeze import jev, squeeze  # noqa: E402
from agent_squeeze.messages import estimate_tokens  # noqa: E402

SPLIT_NEEDLE = "SPLIT_NEEDLE_9ZQ4"
WHOLE_NEEDLE = "WHOLE_NEEDLE_2KX7"
FRAG_LEN = 6


def build_long_line(split_at, line_len=4400):
    """One-line minified-JSON-like payload. split_at = char offset where
    SPLIT_NEEDLE starts; WHOLE_NEEDLE sits early as a control."""
    chars = ["x"] * line_len
    chars[200:200 + len(WHOLE_NEEDLE)] = list(WHOLE_NEEDLE)
    chars[split_at:split_at + len(SPLIT_NEEDLE)] = list(SPLIT_NEEDLE)
    return "".join(chars)


def build_messages(split_at):
    tool_result = (
        "Traceback (most recent call last):\n"
        '  File "runner.py", line 88, in main\n'
        "    raise ValueError(\"payload too large\")\n"
        "ValueError: payload too large\n"
        "--- tool output (minified JSON, one line) ---\n"
        + build_long_line(split_at)
        + "\nFAILED tests/test_payload.py::test_large - ValueError: payload too large\n"
    )
    return [
        {"role": "user", "content": "investigate the failing payload test"},
        {"role": "assistant", "content": "running the payload test now",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "run_test", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "run_test",
         "content": tool_result},
    ]


def _judge_factory(needle, fragment_aware):
    needle_l = needle.lower()
    frags = {needle_l[i:i + FRAG_LEN] for i in range(len(needle_l) - FRAG_LEN + 1)}

    def judge(chunks, task):
        if fragment_aware:
            probs = [0.95 if frags & set(
                chunks[i].lower()[j:j + FRAG_LEN]
                for j in range(len(chunks[i]) - FRAG_LEN + 1))
                     else 0.05 for i in range(len(chunks))]
        else:
            probs = [0.95 if needle_l in chunks[i].lower() else 0.05
                     for i in range(len(chunks))]
        return probs, 0.0

    return judge


def recall(messages):
    blob = json.dumps(messages)
    return {SPLIT_NEEDLE: SPLIT_NEEDLE in blob,
            WHOLE_NEEDLE: WHOLE_NEEDLE in blob}


def run_case(split_at, fragment_aware):
    msgs = build_messages(split_at)
    label = "frag" if fragment_aware else "perfect"
    out = {}
    for two_tier, name in ((False, "single"), (True, "two")):
        jev.score_chunks = _judge_factory(SPLIT_NEEDLE, fragment_aware)
        new_msgs, stats = squeeze.squeeze_transcript(msgs, "find failures",
                                                     two_tier=two_tier)
        before = sum(estimate_tokens(m.get("content", "")) for m in msgs)
        out[name] = {
            "recall": recall(new_msgs),
            "reduction_pct": stats["reduction_pct"],
            "chunks_total": stats["chunks_total"],
            "chunks_kept": stats["chunks_kept"],
            "tokens_before": before,
        }
    print("needle@%d judge=%s" % (split_at, label))
    for name in ("single", "two"):
        r = out[name]
        print("  %-6s split_recall=%-5s whole_recall=%-5s red=%5.1f%% "
              "chunks=%d/%d" % (name, r["recall"][SPLIT_NEEDLE],
                                r["recall"][WHOLE_NEEDLE],
                                r["reduction_pct"], r["chunks_kept"],
                                r["chunks_total"]))
    return out


def main():
    # Needle start offsets: 1495 straddles the 1500 hard-split boundary
    # (chars 1500..1500+len-1); 1000 sits fully inside chunk 1 (control).
    results = {}
    for split_at in (1495, 1000):
        for fragment_aware in (False, True):
            results[(split_at, fragment_aware)] = run_case(split_at,
                                                           fragment_aware)
    # The regression question: with the perfect (fragment-blind) judge,
    # does two-tier lose the split needle while single-tier keeps it?
    perfect = results[(1495, False)]
    reg = (perfect["single"]["recall"][SPLIT_NEEDLE] and
           not perfect["two"]["recall"][SPLIT_NEEDLE])
    print("\nregression (perfect judge): two-tier lost the split needle "
          "that single-tier kept:", reg)
    # And the control: needle fully inside a chunk must survive both tiers.
    ctl = results[(1000, False)]
    print("control (needle inside chunk): single=%s two=%s" %
          (ctl["single"]["recall"][SPLIT_NEEDLE],
           ctl["two"]["recall"][SPLIT_NEEDLE]))


if __name__ == "__main__":
    main()
