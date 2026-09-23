"""Tests for agent_squeeze.ttl (plain asserts, no pytest)."""
from agent_squeeze.ttl import simulate_session, recommend_ttl


def test_all_fast_gaps_hit_5min():
    r = simulate_session([0, 30, 60, 45], 50_000, 2_000, 3.0, "5min")
    assert r["cache_hits"] == 3 and r["cache_misses"] == 1, r
    assert r["hit_rate"] == 0.75, r


def test_slow_gaps_miss_5min():
    r = simulate_session([0, 10 * 60, 10 * 60], 50_000, 2_000, 3.0, "5min")
    assert r["cache_hits"] == 0 and r["cache_misses"] == 3, r


def test_1hour_write_costs_double():
    # Single turn: only the write happens; 1h write ratio (2.0) > 5min (1.25).
    a = simulate_session([0], 50_000, 0, 3.0, "5min")["cost_usd"]
    b = simulate_session([0], 50_000, 0, 3.0, "1hour")["cost_usd"]
    assert abs(b / a - 2.0 / 1.25) < 1e-6, (a, b)


def test_read_refreshes_ttl():
    # Gap pattern 4min, 4min, 4min: every turn is within 5 min of the last
    # *use*, so a 5-min entry refreshed by reads survives 12 minutes.
    r = simulate_session([0, 240, 240, 240], 50_000, 2_000, 3.0, "5min")
    assert r["cache_hits"] == 3, r


def test_recommendation_picks_burst_5min():
    gaps = [0, 30, 45, 60, 40, 90]
    r = recommend_ttl(gaps, 50_000, 2_000, 3.0)
    assert r["recommended"] == "5min", r


def test_recommendation_picks_slow_1hour():
    gaps = [0, 20 * 60, 25 * 60, 15 * 60, 30 * 60, 12 * 60]
    r = recommend_ttl(gaps, 200_000, 2_000, 3.0)
    assert r["recommended"] == "1hour", r
    assert r["saving_pct"] > 0, r


def test_zero_prefix_tie_goes_5min():
    r = recommend_ttl([0, 3600], 0, 2_000, 3.0)
    assert r["recommended"] == "5min", r
