"""Prune an input transcript with Laya (local decision model).

LOWEST PRIORITY method -- run last. Uses the exact config that won the 36-cell
matrix (/tmp/laya_headroom_v2/REPORT.md): ISOLATED per-chunk noul (one chunk per
forward pass; joint context caused primacy bias on this checkpoint) +
RANK-BASED top-K keep (absolute cutoffs are uncalibrated on this checkpoint).

Usage (note the MANDATORY env -- proxy vars break HF downloads, Xet 403s):
    env -u no_proxy -u NO_PROXY HF_HUB_DISABLE_XET=1 HF_HUB_OFFLINE=1 \\
        ~/workspace/laya/venv/bin/python pruners/laya_prune.py \\
        inputs/sre_incident.json outputs/sre_incident_laya.json

  - Chunks each tool-result message into ~250-token slices (1000 chars).
  - One system_one noul call per chunk: "Is this chunk needed to answer the
    user question?" with judge framing + question in the state.
  - Keeps top 25% of chunks by P (rank-based). Fail-safe: never drops a tool
    message entirely (keeps its top chunk).
  - ~2.5s per chunk on CPU; keep total chunks <= ~60 per input.
  - Output JSON schema matches pruners/headroom_prune.py exactly.
"""
import json
import os
import sys
import time

# Defensive: proxy env vars break httpx inside laya/hf_hub; Xet endpoint 403s
# anonymously. Harmless if the caller already set them via `env -u`.
for _v in ("no_proxy", "NO_PROXY"):
    os.environ.pop(_v, None)
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import laya  # noqa: E402

CHUNK_CHARS = 1000  # ~250 tokens
KEEP_FRACTION = 0.25

FRAMING = (
    "You are a precise context-pruning judge for an AI agent. "
    "Decide whether the transcript chunk below is REQUIRED to answer the user's question. "
    "Keep error rows, anomalies, outliers, and anything the answer depends on. "
    "Drop boilerplate: heartbeats, health checks, routine info-level noise."
)


def chunk_text(text, max_chars=CHUNK_CHARS):
    """Split into ~max_chars slices. Splits on newlines first, then hard-splits
    any over-long line (tool contents are single-line JSON, so newline-only
    splitting would yield one giant chunk per message)."""
    pieces = []
    for line in text.split("\n"):
        while len(line) > max_chars:
            pieces.append(line[:max_chars])
            line = line[max_chars:]
        if line.strip():
            pieces.append(line)
    return pieces


def main():
    if len(sys.argv) != 3:
        print("usage: laya_prune.py <input.json> <output.json>", file=sys.stderr)
        sys.exit(2)
    in_path, out_path = sys.argv[1], sys.argv[2]
    with open(in_path) as f:
        doc = json.load(f)

    tool_msgs = [m for m in doc["messages"] if m.get("role") == "tool"]
    chunks = []  # (tool_idx, chunk_text)
    for ti, m in enumerate(tool_msgs):
        for c in chunk_text(m.get("content", "")):
            chunks.append((ti, c))
    print(f"laya: {len(chunks)} chunks to score (~{len(chunks) * 2.5:.0f}s on CPU)",
          flush=True)
    if len(chunks) > 60:
        print("WARNING: >60 chunks, this will take a long time", flush=True)

    print("laya: loading model...", flush=True)
    agent = laya.load("convaiinnovations/laya")

    probs = []
    t = time.time()
    for i, (_, text) in enumerate(chunks):
        state = FRAMING + "\n\nUser question: " + doc["question"] + "\n\nChunk:\n" + text
        ans = agent.system_one(state, {
            "keep": {"type": "noul",
                     "instructions": "Is this chunk needed to answer the user question? "
                                     "Answer true or false."}
        })
        p = ans["answers"]["keep"]["noul"]
        probs.append((i, p))
        if (i + 1) % 10 == 0:
            print(f"laya: scored {i + 1}/{len(chunks)}", flush=True)
    latency = time.time() - t

    probs.sort(key=lambda x: -x[1])
    k = max(1, int(len(probs) * KEEP_FRACTION))
    keep = {i for i, _ in probs[:k]}
    # Fail-safe: never drop a tool message entirely.
    by_tool = {}
    for i, (ti, _) in enumerate(chunks):
        by_tool.setdefault(ti, []).append(i)
    pmap = dict(probs)
    for ti, idxs in by_tool.items():
        if not any(i in keep for i in idxs):
            keep.add(max(idxs, key=lambda i: pmap[i]))

    kept_by_tool = {ti: [] for ti in range(len(tool_msgs))}
    for i in sorted(keep):
        ti, text = chunks[i]
        kept_by_tool[ti].append(text)
    new_tool_msgs = []
    for ti, m in enumerate(tool_msgs):
        kept = kept_by_tool[ti]
        new_content = (
            f"[compressed with laya: kept {len(kept)}/{len(by_tool[ti])} chunks, top-25% rank]\n"
            + "\n".join(kept)
        )
        new_tool_msgs.append({**m, "content": new_content})

    it = iter(new_tool_msgs)
    messages_after = [next(it) if m.get("role") == "tool" else m for m in doc["messages"]]

    chars_before = len(json.dumps(doc["messages"]))
    chars_after = len(json.dumps(messages_after))
    out = {
        "input_id": doc["id"],
        "method": "laya",
        "question": doc["question"],
        "evidence": doc["evidence"],
        "expected_answer_contains": doc.get("expected_answer_contains", []),
        "messages_before": doc["messages"],
        "messages_after": messages_after,
        "stats": {
            "latency_s": round(latency, 2),
            "cost_usd": 0.0,
            "chunks_total": len(chunks),
            "chunks_kept": len(keep),
            "chars_before": chars_before,
            "chars_after": chars_after,
            "token_reduction_pct": round(100 * (chars_before - chars_after) / chars_before, 2),
        },
    }
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f)
    print(f"laya: kept {len(keep)}/{len(chunks)} chunks, "
          f"{out['stats']['token_reduction_pct']:.1f}% reduction in {latency:.0f}s -> {out_path}")


if __name__ == "__main__":
    main()
