"""Single-transcript squeezing: chunk tool results, Jev keep/drop, reassemble.

Conservative by design (proven on adversarial transcripts): a chunk is dropped
only if Jev says p < 0.5 that it is needed, and a tool result is never emptied
entirely. Dropped chunks leave a small marker so the agent sees structure.
"""
import re
import time

from . import jev
from .messages import estimate_tokens

CHUNK_CHARS = 6000  # ~1500 tokens; Jev handles 64k context easily
ERROR_CHUNK_CHARS = 1500  # two-tier: error-dense regions get finer chunks
KEEP_THRESHOLD = 0.5

# Error-dense text: stack traces, failing assertions, pytest/test output.
# Mirrors the heuristic in context.py (_looks_like_error).
ERROR_DENSE_RE = re.compile(
    r"(?i)(traceback \(most recent call last\)|\berror\b|\bfailed\b|"
    r"\bexception\b|assertionerror|^\s*(FAILED|ERROR|FAIL:)|"
    r"\.py:\d+|raise\s+\w*error)")


def _is_error_dense(text):
    """True when the text carries dense error signal: per-chunk keep/drop
    decisions pay off because the signal lines are sparse inside a sea of
    noise (frame lines, repeated log lines, assertion reprints)."""
    if len(text) < 2 * ERROR_CHUNK_CHARS:
        return False  # too short to benefit from finer chunks
    return bool(ERROR_DENSE_RE.search(text))


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


def squeeze_transcript(messages, task, threshold=KEEP_THRESHOLD, two_tier=True):
    """Returns (new_messages, stats). Only role=="tool" messages are chunked;
    user/assistant text is always kept (it carries intent).

    two_tier: error-dense tool results are chunked at ERROR_CHUNK_CHARS
    (finer keep/drop granularity around sparse signal lines); everything
    else uses CHUNK_CHARS (fewer judge calls). Set False for the legacy
    single-tier behavior.
    """
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    chunks = []  # (msg_pos, chunk_text)
    for pos in tool_idx:
        content = messages[pos].get("content", "")
        size = (ERROR_CHUNK_CHARS
                if two_tier and _is_error_dense(content) else CHUNK_CHARS)
        for c in chunk_text(content, size):
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
