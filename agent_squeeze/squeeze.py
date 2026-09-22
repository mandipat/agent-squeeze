"""Single-transcript squeezing: chunk tool results, Jev keep/drop, reassemble.

Conservative by design (proven on adversarial transcripts): a chunk is dropped
only if Jev says p < 0.5 that it is needed, and a tool result is never emptied
entirely. Dropped chunks leave a small marker so the agent sees structure.
"""
import time

from . import jev
from .messages import estimate_tokens

CHUNK_CHARS = 6000  # ~1500 tokens; Jev handles 64k context easily
KEEP_THRESHOLD = 0.5


def chunk_text(text, max_chars=CHUNK_CHARS):
    """Pack lines into ~max_chars chunks (~1500 tokens). Long single lines
    (e.g. single-line JSON tool output) are hard-split. Never emit per-line
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


def squeeze_transcript(messages, task, threshold=KEEP_THRESHOLD):
    """Returns (new_messages, stats). Only role=="tool" messages are chunked;
    user/assistant text is always kept (it carries intent)."""
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    chunks = []  # (msg_pos, chunk_text)
    for pos in tool_idx:
        for c in chunk_text(messages[pos].get("content", "")):
            chunks.append((pos, c))

    t = time.time()
    if chunks:
        probs, cost = jev.score_chunks([c for _, c in chunks], task)
    else:
        probs, cost = [], 0.0
    latency = time.time() - t

    keep = {i for i, p in enumerate(probs) if p >= threshold}
    # Fail-safe: never empty a tool result entirely — keep its best chunk.
    by_msg = {}
    for i, (pos, _) in enumerate(chunks):
        by_msg.setdefault(pos, []).append(i)
    for pos, idxs in by_msg.items():
        if not any(i in keep for i in idxs):
            keep.add(max(idxs, key=lambda i: probs[i]))

    kept_text = {pos: [] for pos in tool_idx}
    for i in sorted(keep):
        pos, text = chunks[i]
        kept_text[pos].append(text)

    new_messages = []
    for pos, m in enumerate(messages):
        if m.get("role") == "tool":
            kept = kept_text[pos]
            dropped = len(by_msg[pos]) - len(kept)
            marker = (f"[squeezed: kept {len(kept)}/{len(by_msg[pos])} chunks]\n"
                      if dropped else "")
            new_messages.append({**m, "content": marker + "\n".join(kept)})
        else:
            new_messages.append(m)

    before = sum(estimate_tokens(m.get("content", "")) for m in messages)
    after = sum(estimate_tokens(m.get("content", "")) for m in new_messages)
    stats = {
        "tokens_before": before, "tokens_after": after,
        "reduction_pct": round(100 * (before - after) / max(1, before), 2),
        "chunks_total": len(chunks), "chunks_kept": len(keep),
        "latency_s": round(latency, 2), "cost_usd": round(cost, 6),
    }
    return new_messages, stats
