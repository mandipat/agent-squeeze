"""Score compressed outputs: token reduction, evidence recall, latency, cost.

Usage:
    python score.py outputs/                 # score every outputs/*.json
    python score.py outputs/sre_incident_headroom.json

Evidence recall = fraction of the input's evidence strings found as
case-insensitive substrings in the compressed messages_after blob.
Token estimates use chars/4 uniformly so methods are comparable; Headroom's
native tiktoken counts are also shown when present.

Writes a markdown table to stdout and saves results to <dir>/summary.json
(or alongside a single file).
"""
import glob
import json
import os
import sys


def score_one(path):
    with open(path) as f:
        d = json.load(f)
    blob = json.dumps(d["messages_after"]).lower()
    ev = d.get("evidence", [])
    hits = [e for e in ev if e.lower() in blob]
    stats = d.get("stats", {})
    return {
        "input": d.get("input_id"),
        "method": d.get("method"),
        "tokens_before_est": stats.get("chars_before", 0) // 4,
        "tokens_after_est": stats.get("chars_after", 0) // 4,
        "reduction_pct": stats.get("token_reduction_pct"),
        "reduction_pct_native": (
            round(100 * stats["tokens_saved_native"] / stats["tokens_before_native"], 2)
            if stats.get("tokens_before_native") else None
        ),
        "evidence_recall": round(len(hits) / len(ev), 4) if ev else None,
        "evidence_kept": f"{len(hits)}/{len(ev)}",
        "evidence_lost": [e for e in ev if e.lower() not in blob],
        "latency_s": stats.get("latency_s"),
        "cost_usd": stats.get("cost_usd"),
        "file": os.path.basename(path),
    }


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "outputs/"
    if os.path.isdir(target):
        paths = sorted(p for p in glob.glob(os.path.join(target, "*.json"))
                       if os.path.basename(p) != "summary.json")
        summary_path = os.path.join(target, "summary.json")
    else:
        paths = [target]
        summary_path = os.path.splitext(target)[0] + "_score.json"
    rows = [score_one(p) for p in paths]

    print("| input | method | tokens in | tokens out | reduction | evidence recall | latency | cost |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['input']} | {r['method']} | {r['tokens_before_est']} | "
              f"{r['tokens_after_est']} | {r['reduction_pct']}% | "
              f"{r['evidence_kept']} ({r['evidence_recall']}) | {r['latency_s']}s | "
              f"${r['cost_usd']} |")
    for r in rows:
        if r["evidence_lost"]:
            print(f"LOST [{r['input']}/{r['method']}]:")
            for e in r["evidence_lost"]:
                print(f"  - {e[:120]}")
    with open(summary_path, "w") as f:
        json.dump(rows, f, indent=1)
    print(f"\nsummary -> {summary_path}")


if __name__ == "__main__":
    main()
