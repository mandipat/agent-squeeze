"""Tests for the admit-time tool-result gate (all offline, no Jev calls)."""
from .admit import (HOLD, KEEP_FULL, NOTICE, TRIM, HoldStore,
                    admit_session, admit_tool_result, deterministic_policy)


def _long_unique_log(n=400, needle="NEEDLE-7F3A"):
    lines = [f"2026-09-22T10:{i//60:02d}:{i%60:02d}Z worker-{i:03d} "
             f"processed batch {i} rows={1000+i} latency_ms={10+i%50}"
             for i in range(n)]
    lines[2] = f"{lines[2]} marker={needle}-TOP"
    lines[-3] = f"{lines[-3]} marker={needle}-BOTTOM"
    return "\n".join(lines), needle


def test_short_result_kept_full():
    store = HoldStore()
    adm, cost = admit_tool_result("ls", "a.txt\nb.txt\n", store=store)
    assert adm.decision == KEEP_FULL
    assert adm.text == "a.txt\nb.txt\n"
    assert adm.ref is None
    assert store.held_chars() == 0


def test_error_result_kept_full():
    store = HoldStore()
    err = "Traceback (most recent call last):\n" + "x\n" * 600
    adm, _ = admit_tool_result("bash", err, store=store)
    assert adm.decision == KEEP_FULL
    assert adm.text == err


def test_repetitive_log_gets_notice_and_hold_roundtrips():
    store = HoldStore()
    text = "\n".join("heartbeat ok uptime=99.9%" for _ in range(500))
    adm, _ = admit_tool_result("poll", text, store=store)
    assert adm.decision == NOTICE
    assert adm.ref is not None
    assert adm.ref in adm.text
    # nothing lost: byte-identical roundtrip
    assert store.readmit(adm.ref) == text


def test_unique_log_trimmed_verbatim_with_needles_intact():
    store = HoldStore()
    text, needle = _long_unique_log()
    adm, _ = admit_tool_result("tail", text, store=store,
                               head_lines=8, tail_lines=8)
    assert adm.decision == TRIM
    assert f"{needle}-TOP" in adm.text
    assert f"{needle}-BOTTOM" in adm.text
    # held middle is byte-identical on readmit
    assert store.readmit(adm.ref) == text
    # trim is strictly smaller
    assert len(adm.text) < len(text)


def test_readmit_if_mentioned():
    store = HoldStore()
    text, _ = _long_unique_log()
    adm, _ = admit_tool_result("tail", text, store=store)
    followup = f"the value I need is in {adm.ref}, please extract it"
    found = store.readmit_if_mentioned(followup)
    assert found == {adm.ref: text}
    assert store.readmit_if_mentioned("no refs here") == {}


def test_hold_decision_admits_ref_only():
    store = HoldStore()
    text = "\n".join(f"row {i} value={i*7}" for i in range(300))
    adm, _ = admit_tool_result("dump", text, store=store,
                               policy_fn=lambda t, e: (HOLD, 0.1))
    assert adm.decision == HOLD
    assert adm.text == f"[tool result held off-context: {adm.ref}]"
    assert store.readmit(adm.ref) == text


def test_admit_session_stats():
    short = ("ls", "ok\n")
    rep = ("poll", "\n".join("heartbeat ok" for _ in range(300)))
    longlog, _ = _long_unique_log()
    adms, stats = admit_session([short, rep, ("tail", longlog)])
    assert stats["results"] == 3
    assert stats["reduction_pct"] > 50
    assert stats["decisions"] == {KEEP_FULL: 1, TRIM: 1, NOTICE: 1, HOLD: 0}
    assert stats["held_chars"] > 0
    assert stats["jev_cost_usd"] == 0.0
