"""Jev pass-2 DEDUP: second decisions call over pass-1 kept chunks.
Question is redundancy, not relevance: 'does every fact in this chunk also
appear in other chunks?' State lists ALL chunks so the model can see
cross-chunk duplication. Honest: no evidence-based fail-safe."""
import importlib.util
import json
import os
import sys
import time

spec = importlib.util.spec_from_file_location(
    "jev_prune", os.path.join(os.path.dirname(__file__), "jev_prune.py"))
jp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jp)

DEDUP_FRAMING = (
    "You are a precise redundancy judge for an AI agent transcript. "
    "The transcript was split into numbered chunks (all shown below). "
    "A chunk is REDUNDANT only if every fact it contains also appears in "
    "other chunks. A chunk with ANY unique fact — a value, version, port, "
    "name, or error detail appearing nowhere else — is NOT redundant. "
    "When in doubt, answer false (not redundant)."
)


def main():
    in_path, out_path = sys.argv[1], sys.argv[2]
    doc = json.load(open(in_path))
    tool_msgs = [m for m in doc["messages"] if m.get("role") == "tool"]
    chunks = []  # (tool_idx, chunk_text)
    for ti, m in enumerate(tool_msgs):
        for c in jp.chunk_text(m.get("content", "")):
            chunks.append((ti, c))

    listing = "\n\n".join(f"--- chunk_{i} ---\n{text}" for i, (_, text) in enumerate(chunks))
    state = (DEDUP_FRAMING + "\n\nUser question: " + doc["question"] +
             "\n\nTranscript chunks:\n" + listing)
    questions = {
        f"chunk_{i}": {"type": "noul", "instructions":
                       f"Is chunk_{i} redundant? Answer true or false."}
        for i in range(len(chunks))
    }
    t = time.time()
    resp = jp.decide(state, questions)
    latency = time.time() - t
    probs = [(i, resp["answers"][f"chunk_{i}"]["noul"]) for i in range(len(chunks))]
    cost = (resp.get("usage") or {}).get("cost", 0.0)

    drop = {i for i, p in probs if p >= 0.5}
    # Fail-safe: never drop a tool message entirely.
    by_tool = {}
    for i, (ti, _) in enumerate(chunks):
        by_tool.setdefault(ti, []).append(i)
    for ti, idxs in by_tool.items():
        if all(i in drop for i in idxs):
            worst = min(idxs, key=lambda i: probs[i][1])
            drop.discard(worst)

    kept_by_tool = {ti: [] for ti in range(len(tool_msgs))}
    for i in range(len(chunks)):
        if i not in drop:
            ti, text = chunks[i]
            kept_by_tool[ti].append(text)
    new_tool_msgs = []
    for ti, m in enumerate(tool_msgs):
        kept = kept_by_tool[ti]
        new_content = (f"[compressed with jev-dedup: kept {len(kept)}/{len(by_tool[ti])} chunks]\n"
                       + "\n".join(kept))
        new_tool_msgs.append({**m, "content": new_content})
    it = iter(new_tool_msgs)
    messages_after = [next(it) if m.get("role") == "tool" else m for m in doc["messages"]]

    chars_before = len(json.dumps(doc["messages"]))
    chars_after = len(json.dumps(messages_after))
    out = {
        "input_id": doc["id"], "method": "jev-dedup", "question": doc["question"],
        "evidence": doc["evidence"],
        "expected_answer_contains": doc.get("expected_answer_contains", []),
        "messages_before": doc["messages"], "messages_after": messages_after,
        "stats": {
            "latency_s": round(latency, 2), "cost_usd": round(cost, 6),
            "chunks_total": len(chunks), "chunks_kept": len(chunks) - len(drop),
            "chars_before": chars_before, "chars_after": chars_after,
            "token_reduction_pct": round(100 * (chars_before - chars_after) / chars_before, 2),
        },
    }
    json.dump(out, open(out_path, "w"))
    print(f"jev-dedup: kept {len(chunks)-len(drop)}/{len(chunks)} chunks, "
          f"{out['stats']['token_reduction_pct']}% reduction, "
          f"${cost:.6f} in {latency:.1f}s -> {out_path}")


main()
