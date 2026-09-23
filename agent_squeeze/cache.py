"""Cache-aware squeezing: protect a stable prompt prefix so provider prompt
caches survive pruning.

Why: Anthropic / OpenAI / Gemini prompt caches key on *exact prefix bytes*.
Any rewrite of an earlier message breaks the whole cache entry, so the next
call re-encodes the full context at full price. Compaction is the #1
cache-killer in long sessions; pruning oldest tool outputs while keeping the
message structure intact (rather than summarizing the prefix) lets the cache
survive. Sources: floppa2003 prompt-caching-playbook, bm629 token-optimization
SKILL.md, papr-ai PROMPT_CACHE_AND_COST_OPTIMIZATION.md — see bench/overnight/PROGRESS.md.

Strategy: keep the first `protect_tokens` of the transcript byte-identical
(the stable prefix: system prompt, tool defs, early history) and squeeze only
the dynamic tail. The next API call then reads the protected prefix from cache
(Anthropic: 0.1x input price) instead of paying full price on a cache miss.
"""
from . import jev
from .messages import estimate_tokens
from .squeeze import (CHUNK_CHARS, ERROR_CHUNK_CHARS, KEEP_THRESHOLD,
                      _is_error_dense, _kept_parts, chunk_text_breaks,
                      chunk_text_breaks_overlap, reassemble_kept)

# Anthropic pricing ratios used by the offline cost model below.
CACHE_READ_RATIO = 0.1    # cache reads cost 0.1x of base input
CACHE_WRITE_RATIO = 1.25  # cache writes cost 1.25x of base input


def split_protected(messages, protect_tokens):
    """Split into (prefix, tail). Prefix is the longest run of whole messages
    whose cumulative tokens fit inside protect_tokens — a message is never
    split, because cache breakpoints sit on message boundaries."""
    cum, cut = 0, 0
    for i, m in enumerate(messages):
        t = estimate_tokens(m.get("content", ""))
        if cum + t <= protect_tokens:
            cum += t
            cut = i + 1
        else:
            break
    return messages[:cut], messages[cut:]


def squeeze_with_policy(messages, task, policy_fn,
                        threshold=KEEP_THRESHOLD, two_tier=True,
                        overlap_chars=0):
    """Same conservative chunk/keep logic as squeeze.squeeze_transcript, but
    keep/drop decisions come from `policy_fn(chunk_texts, task) -> probs`
    instead of Jev. Used for offline benchmarks; swap in jev.score_chunks
    for production.

    two_tier: error-dense tool results are chunked at ERROR_CHUNK_CHARS
    (mirrors squeeze.squeeze_transcript); False keeps the legacy
    single-tier CHUNK_CHARS chunking.
    overlap_chars: > 0 adds an overlap window on hard-split chunks
    (squeeze.chunk_text_breaks_overlap) so fragment-blind judges see
    boundary-straddling needles whole; stripped on reassembly."""
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    chunks = []  # (msg_pos, chunk_text, hard_after, overlap_after)
    for pos in tool_idx:
        content = messages[pos].get("content", "")
        size = (ERROR_CHUNK_CHARS
                if two_tier and _is_error_dense(content) else CHUNK_CHARS)
        if overlap_chars > 0:
            parts = chunk_text_breaks_overlap(content, size, overlap_chars)
        else:
            parts = [(c, hard, 0)
                     for c, hard in chunk_text_breaks(content, size)]
        for c, hard, ov in parts:
            chunks.append((pos, c, hard, ov))

    if chunks:
        probs, cost = policy_fn([c for _, c, _, _ in chunks], task)
    else:
        probs, cost = [], 0.0

    keep = {i for i, p in enumerate(probs) if p >= threshold}
    by_msg = {}
    for i, (pos, _, _, _) in enumerate(chunks):
        by_msg.setdefault(pos, []).append(i)
    for pos, idxs in by_msg.items():
        if not any(i in keep for i in idxs):
            keep.add(max(idxs, key=lambda i: probs[i]))

    kept_text = _kept_parts(chunks, keep, tool_idx)

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
    return new_messages, {"chunks_total": len(chunks),
                          "chunks_kept": len(keep), "cost_usd": cost}


def squeeze_cache_aware(messages, task, protect_tokens=1024,
                        policy_fn=None, threshold=KEEP_THRESHOLD,
                        two_tier=True, overlap_chars=0):
    """Returns (new_messages, stats). The protected prefix is returned
    byte-identical; only the tail is squeezed. `policy_fn` defaults to
    jev.score_chunks. `two_tier` enables error-dense finer chunking on the
    tail (mirrors squeeze.squeeze_transcript); `overlap_chars` adds an
    overlap window on hard-split chunks (fragment-blind-judge rescue)."""
    policy = policy_fn or jev.score_chunks
    prefix, tail = split_protected(messages, protect_tokens)
    squeezed_tail, tstats = squeeze_with_policy(tail, task, policy,
                                               threshold=threshold,
                                               two_tier=two_tier,
                                               overlap_chars=overlap_chars)
    new_messages = list(prefix) + squeezed_tail

    before = sum(estimate_tokens(m.get("content", "")) for m in messages)
    after = sum(estimate_tokens(m.get("content", "")) for m in new_messages)
    protected = sum(estimate_tokens(m.get("content", "")) for m in prefix)
    stats = {
        "tokens_before": before, "tokens_after": after,
        "reduction_pct": round(100 * (before - after) / max(1, before), 2),
        "protected_tokens": protected,          # byte-identical: cache hits
        "dynamic_tokens_after": after - protected,
        "chunks_total": tstats["chunks_total"],
        "chunks_kept": tstats["chunks_kept"],
        "cost_usd": round(tstats["cost_usd"], 6),
    }
    return new_messages, stats


def stable_prefix_tokens(original, squeezed):
    """Count leading tokens that are byte-identical between the two
    transcripts — these are the tokens a provider cache can serve on the next
    call (0.1x price on Anthropic)."""
    stable = 0
    for a, b in zip(original, squeezed):
        if a.get("content", "") == b.get("content", ""):
            stable += estimate_tokens(a.get("content", ""))
        else:
            break
    return stable


def next_turn_cost_model(squeezed, original, base_per_mtok,
                         protected=None):
    """Simulate the *next* API call's input cost after this squeeze pass.

    Cache-aware: the byte-identical leading prefix is served from cache at
    CACHE_READ_RATIO; everything else pays full price.
    Naive (prefix rewritten): 0 stable bytes -> full price on everything.
    Returns dict with stable_tokens, cost_next_usd, cost_next_naive_usd.
    """
    stable = (stable_prefix_tokens(original, squeezed) if protected is None
              else protected)
    total = sum(estimate_tokens(m.get("content", "")) for m in squeezed)
    aware = (stable * CACHE_READ_RATIO + (total - stable)) \
        * base_per_mtok / 1e6
    naive = total * base_per_mtok / 1e6
    return {"stable_tokens": stable, "tokens_total": total,
            "cost_next_aware_usd": round(aware, 6),
            "cost_next_naive_usd": round(naive, 6),
            "cache_saving_pct": round(100 * (naive - aware) / max(naive, 1e-9), 2)}


def session_cost_model(squeezed, original, base_per_mtok, turns=10):
    """Project input cost over `turns` follow-up calls of the same size.

    Cache-aware: the stable prefix is written once (CACHE_WRITE_RATIO) then
    read at CACHE_READ_RATIO every turn. Naive: the rewritten prefix misses
    the cache every turn and pays full price — this is the compounding cost
    of cache-breaking compaction."""
    m = next_turn_cost_model(squeezed, original, base_per_mtok)
    stable, total = m["stable_tokens"], m["tokens_total"]
    write = stable * CACHE_WRITE_RATIO * base_per_mtok / 1e6
    aware = write + turns * (stable * CACHE_READ_RATIO + (total - stable)) \
        * base_per_mtok / 1e6
    naive = turns * total * base_per_mtok / 1e6
    return {"turns": turns,
            "session_aware_usd": round(aware, 4),
            "session_naive_usd": round(naive, 4),
            "session_saving_pct": round(100 * (naive - aware) / max(naive, 1e-9), 2)}


def cache_breakpoints(new_messages, protect_tokens):
    """Suggest Anthropic cache_control breakpoint message indices (max 2 used):
    end of the protected stable prefix, and the final message (conversation
    prefix, slides forward each turn). Returns list of int indices."""
    pts = []
    cum = 0
    for i, m in enumerate(new_messages):
        cum += estimate_tokens(m.get("content", ""))
        if cum <= protect_tokens:
            pts.append(i)
    bps = [pts[-1]] if pts else []
    if new_messages and (not bps or bps[-1] != len(new_messages) - 1):
        bps.append(len(new_messages) - 1)
    return bps


def inject_cache_control(anthropic_messages, breakpoint_indices):
    """Copy of Anthropic-style messages ({"role", "content": [{"type": "text",
    "text": ...}, ...]}) with cache_control {"type": "ephemeral"} added to the
    last text block of each breakpoint message."""
    out = []
    for i, m in enumerate(anthropic_messages):
        m = {**m, "content": [dict(b) for b in m.get("content", [])]}
        if i in breakpoint_indices and m["content"]:
            for b in reversed(m["content"]):
                if b.get("type") == "text":
                    b["cache_control"] = {"type": "ephemeral"}
                    break
        out.append(m)
    return out
