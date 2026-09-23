"""Two-tier regression bench: run real adversarial fixtures through squeeze.

Question from Run 20: does two-tier chunking cause any evidence regression
on the adversarial transcripts (dup_tools, mixed_grind, skill_loads,
sre_incident, real_task2, codebase_exploration, github_triage)?

Method: deterministic. Stub the Jev judge as a PERFECT evidence judge —
keep a chunk iff it contains any evidence string verbatim (case-insensitive).
With a perfect judge the comparison isolates the chunking policy:
  - recall must be 1.0/1.0 in BOTH modes (a needle split across a chunk
    boundary would be lost in both cases — that is the regression risk);
  - reduction delta shows where finer chunking on error-dense results
    pays off on real transcripts.

No Jev, no OpenRouter, fully offline.

Run: python bench_chunk_tiers.py
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agent_squeeze import jev, squeeze  # noqa: E402
from agent_squeeze.messages import estimate_tokens  # noqa: E402

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


def run_fixture(path):
    with open(path) as f:
        doc = json.load(f)
    msgs, evidence = doc["messages"], doc["evidence"]
    task = doc.get("question", "")
    rows = {}
    for mode in ("single", "two"):
        real = jev.score_chunks
        jev.score_chunks = _needle_judge_factory(evidence)
        try:
            out, stats = squeeze.squeeze_transcript(
                msgs, task, two_tier=(mode == "two"))
        finally:
            jev.score_chunks = real
        hits, total = recall(out, evidence)
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
        }
    return rows


def main():
    paths = sorted(glob.glob(os.path.join(INPUTS_DIR, "*.json")))
    print("| fixture | tokens | err-dense | single-tier red% (calls) "
          "| two-tier red% (calls) | delta pts | recall (s, t) |")
    print("|---|---|---|---|---|---|---|")
    summary = []
    for p in paths:
        rows = run_fixture(p)
        s, t = rows["single"], rows["two"]
        delta = round(t["reduction_pct"] - s["reduction_pct"], 2)
        assert s["recall"] == t["recall"] == s["recall"].split("/")[1] + "/" + s["recall"].split("/")[1], \
            f"recall mismatch on {s['id']}: {s['recall']} vs {t['recall']}"
        print(f"| {s['id']} | {s['tokens_in']} | {t['error_dense_results']} "
              f"| {s['reduction_pct']}% ({s['judge_calls']}) "
              f"| {t['reduction_pct']}% ({t['judge_calls']}) "
              f"| {delta:+.2f} | {s['recall']} |")
        summary.append({"single": s, "two": t, "delta_pts": delta})
    out = os.path.join(os.path.dirname(__file__), "fixture_regression.json")
    with open(out, "w") as f:
        json.dump(summary, f, indent=1)
    print(f"\nsummary -> {out}")


if __name__ == "__main__":
    main()
