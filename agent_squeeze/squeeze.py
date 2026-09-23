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


def chunk_text_breaks(text, max_chars=CHUNK_CHARS):
    """Like chunk_text, but each chunk also reports whether the break AFTER
    it is a hard split of one long line (True) or a normal line boundary
    (False). Reassembly must join hard splits with "" and normal breaks
    with "\\n" — otherwise long single lines (e.g. minified JSON tool output)
    come back with a "\\n" injected where none existed, silently breaking
    any evidence string that straddled the split (Run 24).

    Chunk texts are byte-identical to chunk_text(); only break info is new.
    """
    chunks = []  # (text, hard_after)
    cur, cur_len = [], 0
    for line in text.split("\n"):
        while len(line) > max_chars:
            if cur:
                chunks.append(("\n".join(cur), False))
                cur, cur_len = [], 0
            chunks.append((line[:max_chars], True))  # hard-split segment
            line = line[max_chars:]
        if not line.strip():
            continue
        if cur and cur_len + len(line) + 1 > max_chars:
            chunks.append(("\n".join(cur), False))
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += len(line) + 1
    if cur:
        chunks.append(("\n".join(cur), False))
    return chunks


def chunk_text(text, max_chars=CHUNK_CHARS):
    """Pack lines into ~max_chars chunks (~1500 tokens). Long single lines
    (e.g. single-line JSON tool output) are hard-split. Never emit per-line
    micro-chunks: a judge cannot assess a 40-char line in isolation."""
    return [c for c, _ in chunk_text_breaks(text, max_chars)]


def reassemble_kept(parts):
    """Rejoin kept chunk texts in original order. `parts` is a list of
    (text, hard_after) from chunk_text_breaks. Hard-split segments join
    with "" (they were one line); line-packed chunks join with "\\n".
    A dropped chunk between two kept ones does not change the separator:
    the break type describes the original boundary."""
    out = []
    for j, (text, hard_after) in enumerate(parts):
        out.append(text)
        if j < len(parts) - 1:
            out.append("" if hard_after else "\n")
    return "".join(out)


def squeeze_transcript(messages, task, threshold=KEEP_THRESHOLD, two_tier=True):
    """Returns (new_messages, stats). Only role=="tool" messages are chunked;
    user/assistant text is always kept (it carries intent).

    two_tier: error-dense tool results are chunked at ERROR_CHUNK_CHARS
    (finer keep/drop granularity around sparse signal lines); everything
    else uses CHUNK_CHARS (fewer judge calls). Set False for the legacy
    single-tier behavior.
    """
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    chunks = []  # (msg_pos, chunk_text, hard_after)
    for pos in tool_idx:
        content = messages[pos].get("content", "")
        size = (ERROR_CHUNK_CHARS
                if two_tier and _is_error_dense(content) else CHUNK_CHARS)
        for c, hard in chunk_text_breaks(content, size):
            chunks.append((pos, c, hard))

    t = time.time()
    if chunks:
        probs, cost = jev.score_chunks([c for _, c, _ in chunks], task)
    else:
        probs, cost = [], 0.0
    latency = time.time() - t

    keep = {i for i, p in enumerate(probs) if p >= threshold}
    # Fail-safe: never empty a tool result entirely — keep its best chunk.
    by_msg = {}
    for i, (pos, _, _) in enumerate(chunks):
        by_msg.setdefault(pos, []).append(i)
    for pos, idxs in by_msg.items():
        if not any(i in keep for i in idxs):
            keep.add(max(idxs, key=lambda i: probs[i]))

    kept_text = {pos: [] for pos in tool_idx}
    for i in sorted(keep):
        pos, text, hard = chunks[i]
        kept_text[pos].append((text, hard))

    new_messages = []
    for pos, m in enumerate(messages):
        if m.get("role") == "tool":
            kept = kept_text[pos]
            dropped = len(by_msg[pos]) - len(kept)
            marker = (f"[squeezed: kept {len(kept)}/{len(by_msg[pos])} chunks]\n"
                      if dropped else "")
            new_messages.append({**m, "content": marker + reassemble_kept(kept)})
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
