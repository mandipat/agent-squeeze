"""Fleet squeezing for MULTIPLE agents running simultaneously.

The fleet-level win a per-agent compressor cannot get: agents working at the
same time constantly produce IDENTICAL tool outputs (same file Read by 4
agents, same `git log`, same test run). Exact duplicates are stored once —
the first agent keeps the full content, the rest get a reference marker.
Only then does each agent get its own Jev squeeze pass.

Deliberately exact-match only: near-duplicate collapsing is what made
Headroom destroy answer-critical deltas (see benchmarks). We never take
that trade autonomously.
"""
import hashlib
import time

from .messages import estimate_tokens, infer_task
from .squeeze import squeeze_transcript
from .cache import squeeze_cache_aware


def _norm(text):
    return " ".join(text.split())


def _hash(text):
    return hashlib.sha1(_norm(text).encode()).hexdigest()[:12]


def squeeze_fleet(transcripts, task=None, threshold=0.5, min_dup_chars=60,
                  protect_tokens=0, policy_fn=None):
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

    Pass 1 — global exact-dedup across agents (in dict order; first agent wins).
    Pass 2 — per-agent squeeze on what remains.
    """
    t0 = time.time()
    seen = {}  # hash -> (agent_id, msg_pos)
    pass1_markers = 0
    deduped = {}

    for agent_id, messages in transcripts.items():
        new_msgs = []
        for pos, m in enumerate(messages):
            content = m.get("content", "")
            if (m.get("role") == "tool" and len(content) >= min_dup_chars):
                h = _hash(content)
                if h in seen:
                    owner, _ = seen[h]
                    new_msgs.append({**m, "content":
                        f"[shared context: identical output already in "
                        f"agent '{owner}' transcript — ref {h}]"})
                    pass1_markers += 1
                    continue
                seen[h] = (agent_id, pos)
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
                                             threshold=threshold)
        else:
            out, stats = squeeze_transcript(messages, agent_task, threshold)
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
        "total_cost_usd": round(total_cost, 6),
        "latency_s": round(time.time() - t0, 2),
    }
    return squeezed, report
