"""Two-tier x cache-aware bench (Run 22).

Question from Run 21: does the cache-aware path benefit from two-tier
chunking like the plain path does, and does it change cache-hit rate?

Method: deterministic, fully offline. Run all bench/inputs/*.json fixtures
through squeeze_cache_aware (protect_tokens=1024) with a PERFECT evidence
judge (keep chunk iff it contains any evidence string, case-insensitive),
in single-tier and two-tier modes. Compare reduction %, judge calls,
evidence recall, and cache-relevant stats (protected/stable tokens, and
next-turn cost via next_turn_cost_model with a $3/MTok base).

Note: the perfect judge is deterministic per chunk text, so recall
differences isolate the chunking policy; the fail-safe (never empty a tool
result) is active in both modes, as in production.

Run: python bench_cache_aware_tiers.py
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agent_squeeze import cache, squeeze  # noqa: E402

INPUTS_DIR = os.path.join(os.path.dirname(__file__), "..", "inputs")


def _needle_judge_factory(evidence):
    needles = [e.lower() for e in evidence]

    def judge(chunks, task):
        probs = [0.95 if any(n in c.lower() for n in needles) else 0.05
                 for c in chunks]
        return probs, 0.0

    return judge


def recall(messages, evidence):
    blob = json.dumps(messages).lower()
    hits = sum(1 for e in evidence if e.lower() in blob)
    return hits, len(evidence)


def run_fixture(path, protect_tokens=1024):
    with open(path) as f:
        doc = json.load(f)
    msgs, evidence = doc["messages"], doc["evidence"]
    task = doc.get("question", "")
    rows = {}
    for mode in ("single", "two"):
        out, stats = cache.squeeze_cache_aware(
            msgs, task, protect_tokens=protect_tokens,
            policy_fn=_needle_judge_factory(evidence),
            two_tier=(mode == "two"))
        hits, total = recall(out, evidence)
        nxt = cache.next_turn_cost_model(out, msgs, 3.0)
        err_dense = sum(1 for m in msgs
                        if m.get("role") == "tool"
                        and squeeze._is_error_dense(m.get("content", "")))
        rows[mode] = {
            "id": doc["id"],
            "tokens_in": stats["tokens_before"],
            "tokens_out": stats["tokens_after"],
            "reduction_pct": stats["reduction_pct"],
            "judge_calls": stats["chunks_total"],
            "recall": f"{hits}/{total}",
            "error_dense_results": err_dense,
            "protected_tokens": stats["protected_tokens"],
            "stable_tokens": nxt["stable_tokens"],
            "next_turn_usd": nxt["cost_next_aware_usd"],
        }
    return rows


def main():
    paths = sorted(glob.glob(os.path.join(INPUTS_DIR, "*.json")))
    hdr = (f"{'fixture':<22} {'err-dense':>9} {'single':>9} {'two-tier':>9} "
           f"{'delta':>8} {'recall':>7} {'next-$':>10} {'next-$':>10}")
    print(hdr)
    print(f"{'':<22} {'results':>9} {'reduct%':>9} {'reduct%':>9} {'(pts)':>8} "
          f"{'s/t':>7} {'(single)':>10} {'(two)':>10}")
    for path in paths:
        rows = run_fixture(path)
        s, t = rows["single"], rows["two"]
        delta = t["reduction_pct"] - s["reduction_pct"]
        print(f"{t['id']:<22} {t['error_dense_results']:>9} "
              f"{s['reduction_pct']:>8.1f}% {t['reduction_pct']:>8.1f}% "
              f"{delta:>+7.1f} {t['recall']:>7} "
              f"{s['next_turn_usd']:>10.6f} {t['next_turn_usd']:>10.6f}")
        if s["recall"] != t["recall"]:
            print(f"  !! recall differs: single={s['recall']} two={t['recall']}")
        if s["stable_tokens"] != t["stable_tokens"]:
            print(f"  !! stable tokens differ: {s['stable_tokens']} vs "
                  f"{t['stable_tokens']} (cache-hit impact)")


if __name__ == "__main__":
    main()
