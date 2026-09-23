"""Offline benchmark for prompt-cache TTL tuning (agent_squeeze.ttl).

Zero paid calls: the simulation is pure arithmetic over deterministic
inter-turn gap patterns. Answers: for a given session rhythm and protected
prefix size, is Anthropic's 5-minute or 1-hour cache TTL cheaper?

Session archetypes (gap in seconds before each turn):
- coding_burst: fast turn-taking, never idles long (CLI/agent loops).
- research_slow: reads a page between turns; gaps often exceed 5 min.
- mixed: bursts separated by 45-min review pauses.
- overnight_idle: agent left running, polls every few hours.

Prefixes: 10k (small skill/system), 50k (large fleet transcript prefix),
200k (huge research/compaction prefix). Dynamic tail 2k tokens/turn.
Base price: $3/MTok (Sonnet-class).
"""
from agent_squeeze.ttl import recommend_ttl, simulate_session

BASE = 3.0
DYNAMIC = 2000

ARCHETYPES = {
    "coding_burst": [0, 30, 45, 60, 40, 90, 50, 35, 70, 55, 80, 65],
    "research_slow": [0, 8*60, 12*60, 6*60, 20*60, 9*60, 15*60, 7*60,
                      25*60, 11*60, 5*60, 14*60],
    "mixed": [0, 40, 50, 45, 45*60, 60, 55, 50*60, 45, 55, 40*60, 50],
    "overnight_idle": [0, 3*3600, 4*3600, 6*3600, 3*3600, 5*3600],
}
PREFIXES = [10_000, 50_000, 200_000]


def main():
    print(f"{'session':<14} {'prefix':>9} {'TTL':>6} {'5min $':>9} "
          f"{'1h $':>9} {'saves':>7} {'hit5':>6} {'hit1h':>6}")
    rows = []
    for name, gaps in ARCHETYPES.items():
        for prefix in PREFIXES:
            r = recommend_ttl(gaps, prefix, DYNAMIC, BASE)
            rows.append((name, prefix, r))
            print(f"{name:<14} {prefix:>9,} {r['recommended']:>6} "
                  f"{r['cost_5min_usd']:>9.4f} {r['cost_1hour_usd']:>9.4f} "
                  f"{r['saving_pct']:>6.1f}% {r['hit_rate_5min']:>6.2f} "
                  f"{r['hit_rate_1hour']:>6.2f}")
    print()
    # Breakeven note: constant-gap sweep on a 50k prefix shows the flip point.
    print("breakeven sweep (50k prefix, 12 turns, constant gap):")
    for gap_min in [2, 5, 8, 15, 30, 60]:
        gaps = [0] + [gap_min * 60] * 11
        a = simulate_session(gaps, 50_000, DYNAMIC, BASE, "5min")
        b = simulate_session(gaps, 50_000, DYNAMIC, BASE, "1hour")
        win = "5min" if a["cost_usd"] <= b["cost_usd"] else "1hour"
        print(f"  gap={gap_min:>3}min -> 5min ${a['cost_usd']:.4f} "
              f"(hit {a['hit_rate']:.2f}) vs 1h ${b['cost_usd']:.4f} "
              f"(hit {b['hit_rate']:.2f})  => {win}")
    return rows


if __name__ == "__main__":
    main()
