"""Tests: opt-in near-duplicate cross-agent dedup (--allow-near-dup).

Run: python3 test_neardup.py   (from agent_squeeze/)
Deterministic stub policy — no Jev, no paid calls.

The Headroom failure case (FINDINGS.md section 4): three near-identical
config dumps collapsed first-occurrence-wins, destroying needles that
lived only in the LAST dump: version = '3.7.2' and port = 9443.
agent-squeeze's near-dup mode keeps the first dump verbatim AND the
differing lines of every collapsed copy, so the regression that matters
is: those two needles survive at 2/2 in BOTH modes.
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent_squeeze.fleet import squeeze_fleet  # noqa: E402
from agent_squeeze import cli  # noqa: E402


def keep_all(chunks, task):
    return [1.0] * len(chunks), 0.0


def dump(version, port, service):
    lines = [
        "# service config (generated)",
        f"service = '{service}'",
        f"version = '{version}'",
        f"port = {port}",
        "host = '10.0.0.12'",
        "workers = 8",
        "log_level = 'info'",
        "retry_backoff_ms = 250",
        "tls = true",
        "health_check_path = '/healthz'",
        "max_payload_mb = 64",
        "gc_interval_s = 300",
    ]
    # Realistic shared boilerplate: 60 identical lines push Jaccard > 0.9,
    # so the collapse is about the deltas, not about borderline similarity.
    lines += [f"option_{i:03d} = 'default-{i}'" for i in range(60)]
    return "\n".join(lines)


# The needles: present ONLY in the last dump (agent c's), exactly like
# Headroom's mixed_grind case where 0/2 recall was unrecoverable.
NEEDLES = ["version = '3.7.2'", "port = 9443"]

TRANSCRIPTS = {
    "a": [
        {"role": "user", "content": "Agent A: roll out auth service"},
        {"role": "assistant", "content": "reading config"},
        {"role": "tool", "content": dump("3.7.0", 9441, "auth")},
        {"role": "tool", "content": "keepme: auth deploy trace E501"},
    ],
    "b": [
        {"role": "user", "content": "Agent B: roll out billing service"},
        {"role": "assistant", "content": "reading config"},
        {"role": "tool", "content": dump("3.7.1", 9442, "billing")},
        {"role": "tool", "content": "keepme: billing deploy trace E501"},
    ],
    "c": [
        {"role": "user", "content": "Agent C: roll out search service"},
        {"role": "assistant", "content": "reading config"},
        {"role": "tool", "content": dump("3.7.2", 9443, "search")},
        {"role": "tool", "content": "keepme: search deploy trace E501"},
    ],
}


def _stubbed(fn, *a, **kw):
    import agent_squeeze.jev as jev_mod
    real = jev_mod.score_chunks
    jev_mod.score_chunks = keep_all
    try:
        return fn(*a, **kw)
    finally:
        jev_mod.score_chunks = real


def test_neardup_off_by_default():
    out, report = _stubbed(squeeze_fleet, TRANSCRIPTS)
    assert report["global_near_duplicates"] == 0, report
    # Safe default: nothing collapsed, all three dumps kept verbatim.
    blob = json.dumps(out)
    assert NEEDLES[0] in blob and NEEDLES[1] in blob
    for agent, needle in (("a", "3.7.0"), ("b", "3.7.1"), ("c", "3.7.2")):
        assert needle in json.dumps(out[agent]), agent
    print("test_neardup_off_by_default: PASS")


def test_neardup_diff_preserving():
    out, report = _stubbed(squeeze_fleet, TRANSCRIPTS, allow_near_dup=True)
    assert report["global_near_duplicates"] == 2, report  # b and c collapse
    # The Headroom needles survive verbatim inside the diff blocks.
    blob = json.dumps(out)
    assert NEEDLES[0] in blob and NEEDLES[1] in blob, \
        "needles destroyed — this is the Headroom failure mode"
    # First dump kept in full for the fleet; later dumps are markers only.
    # Their *deltas* live verbatim in the marker's diff block (that is the
    # anti-Headroom property), while the shared boilerplate is gone.
    blob_b, blob_c = json.dumps(out["b"]), json.dumps(out["c"])
    assert "version = '3.7.0'" in json.dumps(out["a"])
    assert "option_059 = 'default-59'" not in blob_b
    assert "near-duplicate" in blob_b and "near-duplicate" in blob_c
    assert "version = '3.7.1'" in blob_b      # b's delta, preserved
    assert "port = 9443" in blob_c            # c's needle, preserved
    print("test_neardup_diff_preserving: PASS")


def test_neardup_headroom_case_stays_green():
    # The regression test: Headroom scored 0/2 here; both our modes must
    # score 2/2 — needles surviving somewhere in the fleet.
    for mode in ({}, {"allow_near_dup": True}):
        out, report = _stubbed(squeeze_fleet, TRANSCRIPTS, **mode)
        blob = json.dumps(out).lower()
        recall = sum(1 for n in NEEDLES if n.lower() in blob)
        assert recall == 2, f"{mode}: recall {recall}/2"
    print("test_neardup_headroom_case_stays_green: PASS")


def test_neardup_respects_threshold():
    # Dissimilar outputs never collapse, even with the flag on.
    odd = {"role": "tool", "content": "\n".join(
        f"totally different line {i}" for i in range(50))}
    t = {"a": TRANSCRIPTS["a"], "d": [
        {"role": "user", "content": "Agent D: something else"}, odd]}
    out, report = _stubbed(squeeze_fleet, t, allow_near_dup=True)
    assert report["global_near_duplicates"] == 0, report
    assert "totally different line 49" in json.dumps(out["d"])
    print("test_neardup_respects_threshold: PASS")


def test_neardup_cli_flag():
    d = tempfile.mkdtemp()
    paths = []
    for name in ("a", "b", "c"):
        p = os.path.join(d, f"{name}.json")
        json.dump({"messages": TRANSCRIPTS[name]}, open(p, "w"))
        paths.append(p)
    outp = os.path.join(d, "out.json")
    needlef = os.path.join(d, "needles.txt")
    open(needlef, "w").write("\n".join(NEEDLES) + "\n")
    real = cli.squeeze_fleet
    real_jev = None
    import agent_squeeze.jev as jev_mod
    real_jev = jev_mod.score_chunks
    jev_mod.score_chunks = keep_all
    try:
        argv = ["fleet"] + paths + ["--names", "a,b,c", "-o", outp,
                                    "--allow-near-dup", "--needles", needlef]
        buf = io.StringIO()
        with redirect_stdout(buf):
            old = sys.argv
            sys.argv = ["cli"] + argv
            try:
                cli.main()
            except SystemExit as e:
                assert e.code == 0, f"fleet exited {e.code}: {buf.getvalue()}"
            finally:
                sys.argv = old
        saved = json.load(open(outp))
        assert saved["report"]["global_near_duplicates"] == 2, \
            saved["report"]
        assert "near-duplicates" in buf.getvalue()
        print("test_neardup_cli_flag: PASS")
    finally:
        jev_mod.score_chunks = real_jev


if __name__ == "__main__":
    test_neardup_off_by_default()
    test_neardup_diff_preserving()
    test_neardup_headroom_case_stays_green()
    test_neardup_respects_threshold()
    test_neardup_cli_flag()
