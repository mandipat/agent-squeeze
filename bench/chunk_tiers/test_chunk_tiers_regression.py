"""Regression tests: two-tier chunking never loses evidence on real fixtures.

Runs every bench/inputs/*.json fixture through squeeze_transcript in both
modes with a deterministic perfect-evidence judge (keep chunk iff it holds
an evidence string). Asserts:
  1. recall is complete (n/n) in BOTH modes — no needle split/dropped;
  2. two-tier reduction is never worse than single-tier (policy monotone);
  3. prose-only fixtures are byte-identical across modes (no-op).

Self-contained runner (no pytest in this env). No Jev, no paid calls.

Run: python3 test_chunk_tiers_regression.py
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agent_squeeze import jev, squeeze  # noqa: E402

INPUTS_DIR = os.path.join(os.path.dirname(__file__), "..", "inputs")


def _load(path):
    with open(path) as f:
        doc = json.load(f)
    return doc


def _needle_judge(evidence):
    needles = [e.lower() for e in evidence]

    def judge(chunks, task):
        return [0.95 if any(n in c.lower() for n in needles) else 0.05
                for c in chunks], 0.0

    return judge


def _squeeze_both(doc):
    msgs, task = doc["messages"], doc.get("question", "")
    outs = {}
    for mode in (False, True):
        real = jev.score_chunks
        jev.score_chunks = _needle_judge(doc["evidence"])
        try:
            outs[mode] = squeeze.squeeze_transcript(msgs, task, two_tier=mode)
        finally:
            jev.score_chunks = real
    return outs[False], outs[True]


def _recall(out, evidence):
    blob = json.dumps(out).lower()
    return sum(1 for e in evidence if e.lower() in blob)


def test_full_recall_both_modes_all_fixtures():
    for path in sorted(glob.glob(os.path.join(INPUTS_DIR, "*.json"))):
        doc = _load(path)
        (out1, _), (out2, _) = _squeeze_both(doc)
        n = len(doc["evidence"])
        assert _recall(out1, doc["evidence"]) == n, \
            f"{doc['id']}: single-tier lost evidence"
        assert _recall(out2, doc["evidence"]) == n, \
            f"{doc['id']}: two-tier lost evidence"


def test_two_tier_never_worse_reduction():
    for path in sorted(glob.glob(os.path.join(INPUTS_DIR, "*.json"))):
        doc = _load(path)
        (_, s1), (_, s2) = _squeeze_both(doc)
        assert s2["reduction_pct"] >= s1["reduction_pct"], \
            f"{doc['id']}: two-tier {s2['reduction_pct']}% < single-tier {s1['reduction_pct']}%"


def test_non_error_dense_fixtures_byte_identical():
    # Fixtures with no error-dense tool results must squeeze identically.
    for path in sorted(glob.glob(os.path.join(INPUTS_DIR, "*.json"))):
        doc = _load(path)
        err_dense = any(m.get("role") == "tool"
                        and squeeze._is_error_dense(m.get("content", ""))
                        for m in doc["messages"])
        if err_dense:
            continue
        (out1, _), (out2, _) = _squeeze_both(doc)
        assert out1 == out2, f"{doc['id']}: non-error-dense outputs differ"


def main():
    for name, fn in sorted(
            [(k, v) for k, v in globals().items() if k.startswith("test_")]):
        fn()
        print(f"PASS {name}")


if __name__ == "__main__":
    main()
