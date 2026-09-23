"""Prompt-cache TTL tuning: 5-minute vs 1-hour Anthropic cache TTL.

Why: Runs 1-9 protect a stable prefix so provider prompt caches survive
squeezing, but the *TTL choice* changes what the cache costs. Anthropic's
default 5-min TTL charges a 1.25x write and 0.1x reads; the 1-hour TTL
charges a 2.0x write for the same 0.1x reads. In fast burst sessions the 5-min
entry almost always hits (so the cheaper write wins), while in slow sessions
(turn gaps > 5 min) the 5-min entry expires and the prefix gets rewritten at
full 1.25x price over and over — there the 1-hour write pays off. This module
simulates both TTLs over a session's inter-turn gap pattern and recommends
the cheaper one.

Model: the protected prefix is one cache entry. Each successful read refreshes
the TTL; each turn whose gap from the last use exceeds the TTL misses and
rewrites the prefix at the TTL's write ratio. The dynamic tail (squeezed
transcript tail) always pays full price — it changes every turn.

Prices: ratios only; pass your model's $/MTok (e.g. Sonnet $3, Opus $15).
"""
# Anthropic prompt-caching ratios (per provider docs, 2026).
CACHE_READ_RATIO = 0.1
TTL_POLICIES = {
    "5min": {"ttl_sec": 300, "write_ratio": 1.25},
    "1hour": {"ttl_sec": 3600, "write_ratio": 2.0},
}


def simulate_session(turn_gaps_sec, prefix_tokens, dynamic_tokens,
                     base_per_mtok, ttl_key):
    """Simulate one N-turn session.

    turn_gaps_sec: gap in seconds *before* each turn (first element is the
    gap between session start and turn 1; length == number of turns).
    Every turn pays: prefix (cached per TTL policy) + dynamic tail (full price).
    Returns dict with cost_usd, cache_hits, cache_misses, hit_rate.
    """
    pol = TTL_POLICIES[ttl_key]
    tlast, hits, misses = None, 0, 0
    prefix_cost, dynamic_cost = 0.0, 0.0
    t = 0.0
    for gap in turn_gaps_sec:
        t += max(0.0, gap)
        if tlast is None or (t - tlast) > pol["ttl_sec"]:
            # First turn or TTL expired: write the prefix entry.
            prefix_cost += prefix_tokens * pol["write_ratio"]
            misses += 1
        else:
            prefix_cost += prefix_tokens * CACHE_READ_RATIO
            hits += 1
        dynamic_cost += dynamic_tokens  # tail changes: full price every turn
        tlast = t
    total = (prefix_cost + dynamic_cost) * base_per_mtok / 1e6
    n = hits + misses
    return {"cost_usd": round(total, 6), "cache_hits": hits,
            "cache_misses": misses,
            "hit_rate": round(hits / max(1, n), 3)}


def recommend_ttl(turn_gaps_sec, prefix_tokens, dynamic_tokens,
                  base_per_mtok):
    """Simulate both TTLs; recommend the cheaper one. Ties go to 5min
    (cheaper write, no downside)."""
    sims = {k: simulate_session(turn_gaps_sec, prefix_tokens, dynamic_tokens,
                               base_per_mtok, k)
            for k in TTL_POLICIES}
    winner = min(sims, key=lambda k: sims[k]["cost_usd"])
    loser = "1hour" if winner == "5min" else "5min"
    margin = 0.0
    if sims[loser]["cost_usd"] > 0:
        margin = 100 * (sims[loser]["cost_usd"] - sims[winner]["cost_usd"]) \
            / sims[loser]["cost_usd"]
    return {"recommended": winner, "cost_5min_usd": sims["5min"]["cost_usd"],
            "cost_1hour_usd": sims["1hour"]["cost_usd"],
            "saving_pct": round(margin, 1),
            "hit_rate_5min": sims["5min"]["hit_rate"],
            "hit_rate_1hour": sims["1hour"]["hit_rate"]}
