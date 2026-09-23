"""Fleet squeezing for MULTIPLE agents running simultaneously.

The fleet-level win a per-agent compressor cannot get: agents working at the
same time constantly produce IDENTICAL tool outputs (same file Read by 4
agents, same `git log`, same test run). Exact duplicates are stored once —
the first agent keeps the full content, the rest get a reference marker.
Only then does each agent get its own Jev squeeze pass.

Deliberately exact-match only: near-duplicate collapsing is what made
Headroom destroy answer-critical deltas (see benchmarks). We never take
that trade autonomously. The opt-in ``allow_near_dup`` mode collapses
near-duplicates too — but *diff-preserving*: the first occurrence stays
verbatim and each collapsed copy keeps its differing lines in the marker,
so an answer-critical delta is never unrecoverable (the Headroom
mixed_grind failure case stays green by construction).
"""
import hashlib
import time

from .messages import estimate_tokens, infer_task
from .squeeze import squeeze_transcript
from .cache import squeeze_cache_aware


def _norm(text):
    return " ".join(text.split())


def _jaccard(a, b):
    """Token-set Jaccard on normalized text; 1.0 = identical vocab."""
    sa, sb = set(_norm(a).split()), set(_norm(b).split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _diff_lines(first, later):
    """Lines of `later` absent from `first`, order-preserving.

    This is what makes near-dup collapse safe: unlike Headroom's
    first-occurrence-wins sentinel, every delta survives verbatim in the
    marker, so a needle present only in a later dump (e.g. the last dump's
    ``version`` / ``port``) is never destroyed.
    """
    first_lines = set(first.splitlines())
    return [ln for ln in later.splitlines() if ln not in first_lines]


def _hash(text):
    return hashlib.sha1(_norm(text).encode()).hexdigest()[:12]


def squeeze_fleet(transcripts, task=None, threshold=0.5, min_dup_chars=60,
                  protect_tokens=0, policy_fn=None, two_tier=True,
                  overlap_chars=0, allow_near_dup=False,
                  near_dup_jaccard=0.85):
    """transcripts: {agent_id: [messages]}. Returns (squeezed, report).

    task: a single shared objective (str), a per-agent dict {agent_id: task},
          or None — in which case each agent's task is inferred from its own
          first user message. Per-agent tasks are the correct default: agents
          running simultaneously have different objectives, and the task
          defines what "needed" means to the judge.

    protect_tokens: when > 0, each agent's pass-2 squeeze keeps the first N
          tokens of its transcript byte-identical (cache-aware), so fleets
          in long sessions keep their provider prompt caches warm across
          agents. 0 = classic per-agent squeeze.

    policy_fn: keep/drop policy override (chunk_texts, task) -> (probs, cost).
          Defaults to real Jev when protect_tokens == 0, and to
          jev.score_chunks inside squeeze_cache_aware otherwise. Injectable
          for offline use.

    two_tier: pass through to the per-agent squeeze — error-dense tool
          results get finer chunks (default True; False = legacy uniform
          chunking).

    overlap_chars: pass through to the per-agent squeeze — overlap window on
          hard-split chunks (Run 25's fragment-blind-judge rescue; 0 = off).

    allow_near_dup: opt-in Phase-3 semantic dedup. Near-duplicate tool
          outputs (Jaccard >= near_dup_jaccard) collapse like exact dupes,
          but the marker keeps the *differing lines* verbatim, so the
          Headroom failure case (a needle living only in a later dump)
          stays green. Default False — never autonomous.

    near_dup_jaccard: similarity threshold for near-dup collapse (0–1).

    Pass 1 — global exact-dedup across agents (in dict order; first agent wins).
    With allow_near_dup, a second grouping pass collapses near-duplicates
    with their deltas preserved.
    Pass 2 — per-agent squeeze on what remains.
    """
    t0 = time.time()
    seen = {}  # hash -> (agent_id, msg_pos, full content)
    pass1_markers = 0
    near_markers = 0
    deduped = {}

    for agent_id, messages in transcripts.items():
        new_msgs = []
        for pos, m in enumerate(messages):
            content = m.get("content", "")
            if (m.get("role") == "tool" and len(content) >= min_dup_chars):
                h = _hash(content)
                if h in seen:
                    owner, _, _ = seen[h]
                    new_msgs.append({**m, "content":
                        f"[shared context: identical output already in "
                        f"agent '{owner}' transcript — ref {h}]"})
                    pass1_markers += 1
                    continue
                if allow_near_dup:
                    best, best_j = None, 0.0
                    for h2, (owner2, _, full2) in seen.items():
                        j = _jaccard(full2, content)
                        if j > best_j:
                            best, best_j = (h2, owner2, full2), j
                    if best is not None and best_j >= near_dup_jaccard:
                        h2, owner2, full2 = best
                        diff = _diff_lines(full2, content)
                        diff_block = ("\n".join(diff)[:4000]
                                      if diff else "(no line differences)")
                        new_msgs.append({**m, "content":
                            f"[shared context: near-duplicate of agent "
                            f"'{owner2}' tool output — ref {h2}, similarity "
                            f"{best_j:.2f}; only the differing lines are "
                            f"kept below:\n{diff_block}]"})
                        near_markers += 1
                        continue
                seen[h] = (agent_id, pos, content)
            new_msgs.append(m)
        deduped[agent_id] = new_msgs

    squeezed, per_agent = {}, {}
    total_cost = 0.0
    for agent_id, messages in deduped.items():
        agent_task = (task.get(agent_id) if isinstance(task, dict)
                      else task) or infer_task(messages)
        if protect_tokens > 0:
            out, stats = squeeze_cache_aware(messages, agent_task,
                                             protect_tokens,
                                             policy_fn=policy_fn,
                                             threshold=threshold,
                                             two_tier=two_tier,
                                             overlap_chars=overlap_chars)
        else:
            out, stats = squeeze_transcript(messages, agent_task, threshold,
                                            two_tier=two_tier,
                                            overlap_chars=overlap_chars)
        squeezed[agent_id] = out
        per_agent[agent_id] = stats
        total_cost += stats["cost_usd"]

    def toks(msgs):
        return sum(estimate_tokens(m.get("content", "")) for m in msgs)

    before = sum(toks(m) for m in transcripts.values())
    after = sum(toks(m) for m in squeezed.values())
    report = {
        "agents": per_agent,
        "fleet_tokens_before": before,
        "fleet_tokens_after": after,
        "fleet_reduction_pct": round(100 * (before - after) / max(1, before), 2),
        "global_exact_duplicates": pass1_markers,
        "global_near_duplicates": near_markers,
        "total_cost_usd": round(total_cost, 6),
        "latency_s": round(time.time() - t0, 2),
    }
    return squeezed, report
