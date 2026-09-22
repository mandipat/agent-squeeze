"""Prune an input transcript with Headroom's real compression pipeline.

Usage:
    ~/workspace/compress_bench/venv/bin/python pruners/headroom_prune.py \
        inputs/sre_incident.json outputs/sre_incident_headroom.json

Reads the input JSON ({messages, question, evidence, ...}), runs
headroom.compress() (full pipeline: ContentRouter -> SmartCrusher/Kompress/etc.,
proxy-less) over the OpenAI-format messages, and writes:
    {input_id, method, question, evidence,
     messages_before, messages_after, stats: {latency_s, cost_usd,
        tokens_before_native, tokens_after_native, tokens_saved_native,
        ratio_native, chars_before, chars_after, token_reduction_pct}}
"""
import json
import os
import sys
import time

import headroom

TARGET_RATIO = 0.5


def prune_messages(messages):
    t = time.time()
    result = headroom.compress(messages, model="gpt-4o", target_ratio=TARGET_RATIO)
    return result, time.time() - t


def main():
    if len(sys.argv) != 3:
        print("usage: headroom_prune.py <input.json> <output.json>", file=sys.stderr)
        sys.exit(2)
    in_path, out_path = sys.argv[1], sys.argv[2]
    with open(in_path) as f:
        doc = json.load(f)

    result, latency = prune_messages(doc["messages"])
    compressed = result.messages

    chars_before = len(json.dumps(doc["messages"]))
    chars_after = len(json.dumps(compressed))

    out = {
        "input_id": doc["id"],
        "method": "headroom",
        "question": doc["question"],
        "evidence": doc["evidence"],
        "expected_answer_contains": doc.get("expected_answer_contains", []),
        "messages_before": doc["messages"],
        "messages_after": compressed,
        "stats": {
            "latency_s": round(latency, 2),
            "cost_usd": 0.0,
            "tokens_before_native": result.tokens_before,
            "tokens_after_native": result.tokens_after,
            "tokens_saved_native": result.tokens_saved,
            "ratio_native": round(result.compression_ratio, 4),
            "chars_before": chars_before,
            "chars_after": chars_after,
            "token_reduction_pct": round(100 * (chars_before - chars_after) / chars_before, 2),
        },
    }
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f)
    print(f"headroom: {result.tokens_before} -> {result.tokens_after} native tokens "
          f"({result.compression_ratio:.1%} reduction) in {latency:.1f}s -> {out_path}")


if __name__ == "__main__":
    main()
