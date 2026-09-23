"""Prune an input transcript with Jev (TypeSafe decision model) via OpenRouter.

STUB ONLY -- DO NOT RUN WITHOUT AN API KEY. This script is complete but has
never been executed in this environment. It requires:
    export OPENROUTER_API_KEY="sk-or-v1-..."

Usage:
    python pruners/jev_prune.py inputs/sre_incident.json outputs/sre_incident_jev.json

How it works:
  - Chunks each tool-result message into ~1500-token slices (Jev has 64k
    context, so chunks can be large).
  - Sends ONE POST to https://openrouter.ai/api/alpha/decisions with model
    "typesafe/jev-1.13" and one noul question per chunk, all evaluated in
    parallel against the same state (judge framing + user question).
  - Keeps chunks with calibrated p >= 0.5 (Jev's probabilities are calibrated;
    proven on Headroom's fidelity fixtures: evidence rows scored 0.98).
  - Fail-safe: if a tool message would lose every chunk, keeps its top chunk.
  - Output JSON schema matches pruners/headroom_prune.py exactly.

Verified endpoint notes (from live probing 2026-09-22):
  - Jev is NOT in OpenRouter's /models catalog and does NOT work on
    /chat/completions (400: "decisions model ... use /api/alpha/decisions").
  - POST https://openrouter.ai/api/alpha/decisions
    body: {"model": "typesafe/jev-1.13", "state": <str|dict>,
           "questions": {qid: {"type": "noul", "instructions": str}}}
    response: {"model": "typesafe/jev-1.13-20260917",
               "answers": {qid: {"type": "noul", "noul": 0.0-1.0}},
               "usage": {"input_tokens": n, "output_tokens": n, "cost": usd}}
"""
import json
import os
import sys
import time
import urllib.request

API_URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
CHUNK_CHARS = 6000  # ~1500 tokens; Jev has 64k context

FRAMING = (
    "You are a precise context-pruning judge for an AI agent. "
    "Decide which transcript chunks are REQUIRED to answer the user's question. "
    "Keep error rows, anomalies, outliers, and anything the answer depends on. "
    "Drop boilerplate: heartbeats, health checks, routine info-level noise."
)


def chunk_text(text, max_chars=CHUNK_CHARS):
    """Pack lines into ~max_chars chunks (~1500 tokens). Long single lines
    (tool contents are single-line JSON) are hard-split. Never emit per-line
    micro-chunks: a judge cannot assess a 40-char line in isolation."""
    chunks, cur, cur_len = [], [], 0
    for line in text.split("\n"):
        while len(line) > max_chars:
            if cur:
                chunks.append("\n".join(cur))
                cur, cur_len = [], 0
            chunks.append(line[:max_chars])
            line = line[max_chars:]
        if not line.strip():
            continue
        if cur and cur_len + len(line) + 1 > max_chars:
            chunks.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def decide(state, questions):
    """Call Jev via the openrouter skill (stored credential, authd surrogate)."""
    import subprocess
    import tempfile
    skill = os.path.expanduser("~/workspace/skills/openrouter/bin/or_decide.py")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(questions, f)
        qpath = f.name
    try:
        out = subprocess.run(
            [sys.executable, skill, "--model", MODEL,
             "--state", state, "--questions", qpath],
            capture_output=True, text=True, timeout=300)
    finally:
        os.unlink(qpath)
    if out.returncode:
        raise RuntimeError(f"or_decide.py failed: {out.stderr[-300:]}")
    return json.loads(out.stdout)


def main():
    if len(sys.argv) != 3:
        print("usage: jev_prune.py <input.json> <output.json>", file=sys.stderr)
        sys.exit(2)
    in_path, out_path = sys.argv[1], sys.argv[2]
    with open(in_path) as f:
        doc = json.load(f)

    # Build chunks: only tool-result messages get chunked; everything else kept.
    tool_msgs = [m for m in doc["messages"] if m.get("role") == "tool"]
    other_msgs = [m for m in doc["messages"] if m.get("role") != "tool"]
    chunks = []  # (tool_idx, chunk_text)
    for ti, m in enumerate(tool_msgs):
        for c in chunk_text(m.get("content", "")):
            chunks.append((ti, c))

    state = FRAMING + "\n\nUser question: " + doc["question"]
    questions = {
        f"chunk_{i}": {
            "type": "noul",
            "instructions": (
                f"Is the following transcript chunk needed to answer the user "
                f"question? Answer true or false.\n\nChunk:\n{text}"
            ),
        }
        for i, (_, text) in enumerate(chunks)
    }

    t = time.time()
    resp = decide(state, questions)
    latency = time.time() - t
    probs = [(i, resp["answers"][f"chunk_{i}"]["noul"]) for i in range(len(chunks))]
    cost = (resp.get("usage") or {}).get("cost", 0.0)

    keep = {i for i, p in probs if p >= 0.5}
    # Fail-safe: never drop a tool message entirely.
    by_tool = {}
    for i, (ti, _) in enumerate(chunks):
        by_tool.setdefault(ti, []).append(i)
    for ti, idxs in by_tool.items():
        if not any(i in keep for i in idxs):
            best = max(idxs, key=lambda i: probs[i][1])
            keep.add(best)

    kept_by_tool = {ti: [] for ti in range(len(tool_msgs))}
    for i in sorted(keep):
        ti, text = chunks[i]
        kept_by_tool[ti].append(text)
    new_tool_msgs = []
    for ti, m in enumerate(tool_msgs):
        kept = kept_by_tool[ti]
        new_content = (
            f"[compressed with jev: kept {len(kept)}/{len(by_tool[ti])} chunks]\n"
            + "\n".join(kept)
        )
        new_tool_msgs.append({**m, "content": new_content})

    # Reassemble in original message order.
    it = iter(new_tool_msgs)
    messages_after = [next(it) if m.get("role") == "tool" else m for m in doc["messages"]]

    chars_before = len(json.dumps(doc["messages"]))
    chars_after = len(json.dumps(messages_after))
    out = {
        "input_id": doc["id"],
        "method": "jev",
        "question": doc["question"],
        "evidence": doc["evidence"],
        "expected_answer_contains": doc.get("expected_answer_contains", []),
        "messages_before": doc["messages"],
        "messages_after": messages_after,
        "stats": {
            "latency_s": round(latency, 2),
            "cost_usd": round(cost, 6),
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
    print(f"jev: kept {len(keep)}/{len(chunks)} chunks, "
          f"{out['stats']['token_reduction_pct']:.1f}% reduction, "
          f"${cost:.6f} in {latency:.1f}s -> {out_path}")


if __name__ == "__main__":
    main()
