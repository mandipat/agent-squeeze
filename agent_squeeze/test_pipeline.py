"""Tests for agent_squeeze.pipeline (plain asserts, no pytest)."""
from agent_squeeze.pipeline import (squeeze_pipeline_run, deterministic_policy,
                                    PROGRESS_RE, ERROR_RE)
from agent_squeeze.admit import HoldStore


def _step(name, text):
    return {"name": name, "text": text}


def test_progress_only_becomes_notice():
    step = _step("poll", "\n".join(f"poll #{i}: no new data | heartbeat" for i in range(30)))
    d, _ = deterministic_policy(step, set(), set())
    assert d == "notice", d


def test_error_step_kept_full():
    step = _step("v", "Traceback (most recent call last):\nValueError: negative revenue")
    d, excerpt = deterministic_policy(step, set(), set())
    assert d == "keep_full" and "ValueError" in excerpt


def test_metrics_excerpted_verbatim():
    text = "\n".join(["transform started"] * 30 + [
        "rows processed: 1,000,000", "duration: 88.2s",
        "checksum md5: abc123def456"])
    step = _step("t", text)
    d, excerpt = deterministic_policy(step, set(), set())
    assert d == "keep_excerpt", d
    assert "1,000,000" in excerpt and "abc123def456" in excerpt


def test_duplicate_schema_collapses():
    ddl = "CREATE TABLE t (\n  id BIGINT NOT NULL,\n  PRIMARY KEY (id)\n);"
    pad = "note: extra log detail line\n" * 20
    seen = set()
    d1, _ = deterministic_policy(_step("a", "extract\n" + ddl + "\nrows read: 5\n" + pad), seen, set())
    d2, excerpt = deterministic_policy(_step("b", "transform\n" + ddl + "\nrows read: 6\n" + pad), seen, set())
    assert d1 == "keep_excerpt", d1
    assert d2 == "notice" and "identical" in excerpt, (d2, excerpt)


def test_short_step_kept_full():
    d, excerpt = deterministic_policy(_step("s", "ok"), set(), set())
    assert d == "keep_full" and excerpt == "ok"


def test_squeeze_pipeline_run_stats_and_hold():
    steps = [
        _step("a", "heartbeat\n" * 60),  # pure noise -> notice, held
        _step("b", "rows written: 999\nchecksum md5: aa11bb22"),  # short -> keep_full
    ]
    store = HoldStore()
    out, stats = squeeze_pipeline_run(steps, store=store)
    assert stats["notice"] == 1 and stats["keep_full"] == 1
    assert stats["chars_reduced_pct"] > 50
    ref = out[0]["text"].split("[held:")[1].rstrip("]")
    assert store.readmit(ref) == steps[0]["text"]


def test_repeated_pattern_collapses():
    lines = ["rule_01: ok (no skew)"] * 40 + ["rows processed: 100"]
    d, excerpt = deterministic_policy(_step("t", "\n".join(lines)), set(), set())
    assert d == "keep_excerpt", d
    assert "rows processed: 100" in excerpt


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"{len(fns)}/{len(fns)} passed")
