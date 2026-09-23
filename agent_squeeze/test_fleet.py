"""Tests: --protect-prefix for fleet squeeze (library, CLI, server).

Run: python3 test_fleet.py   (from agent_squeeze/)
Uses a deterministic stub policy — no Jev, no paid calls.
"""
import io
import json
import os
import sys
import tempfile
import threading
import urllib.request
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent_squeeze.fleet import squeeze_fleet  # noqa: E402
from agent_squeeze import cli  # noqa: E402
from agent_squeeze.server import Handler  # noqa: E402


def stub_policy(chunks, task):
    # Keep chunks mentioning "keepme", drop everything else (free).
    probs = [1.0 if "keepme" in c else 0.0 for c in chunks]
    return probs, 0.0


def make_transcripts():
    shared = "shared read of config.yaml\n" + "\n".join(
        f"boilerplate line {i}" for i in range(200))
    a = [
        {"role": "user", "content": "Agent A: migrate auth to JWT"},
        {"role": "assistant", "content": "reading config"},
        {"role": "tool", "content": shared},
        {"role": "tool", "content": "keepme: deploy error traceback E501"},
    ]
    b = [
        {"role": "user", "content": "Agent B: add rate limiting"},
        {"role": "assistant", "content": "reading config"},
        {"role": "tool", "content": shared},  # exact duplicate of A's
        {"role": "tool", "content": "\n".join(
            f"heartbeat ok {i} service nominal no errors" for i in range(600))},
    ]
    return {"a": a, "b": b}


def test_fleet_protect_library():
    t = make_transcripts()
    out, report = squeeze_fleet(t, protect_tokens=100, policy_fn=stub_policy)
    # Cross-agent dedup still works in pass 1
    assert report["global_exact_duplicates"] == 1, report
    blob_b = json.dumps(out["b"])
    assert "[shared context" in blob_b and "config.yaml" not in blob_b, \
        "b's duplicate should be a marker, not full text"
    # Per-agent protected prefix: first messages byte-identical
    for agent in ("a", "b"):
        assert out[agent][0]["content"] == t[agent][0]["content"]
        st = report["agents"][agent]
        assert st["protected_tokens"] > 0, st
        assert st["cost_usd"] == 0.0
    # Tail still squeezed: a keeps its keepme chunk; b's heartbeat chunks
    # are mostly dropped (conservative keep-one-chunk floor keeps exactly 1)
    assert "keepme" in json.dumps(out["a"])
    bst = report["agents"]["b"]
    assert bst["chunks_kept"] < bst["chunks_total"], bst
    print("test_fleet_protect_library: PASS")


def test_fleet_protect_default_unchanged():
    import agent_squeeze.jev as jev_mod
    t = make_transcripts()
    real = jev_mod.score_chunks
    jev_mod.score_chunks = stub_policy
    try:
        out, report = squeeze_fleet(t)
    finally:
        jev_mod.score_chunks = real
    assert "protected_tokens" not in report["agents"]["a"]
    assert report["global_exact_duplicates"] == 1
    print("test_fleet_protect_default_unchanged: PASS")


def test_fleet_cli_protect():
    d = tempfile.mkdtemp()
    pa, pb = os.path.join(d, "a.json"), os.path.join(d, "b.json")
    t = make_transcripts()
    json.dump({"messages": t["a"]}, open(pa, "w"))
    json.dump({"messages": t["b"]}, open(pb, "w"))
    outp = os.path.join(d, "out.json")
    real = cli.squeeze_fleet
    # Stub the Jev path by monkeypatching cache's jev reference is awkward
    # from cli; instead wrap squeeze_fleet to inject the stub policy.
    def wrapped(transcripts, task=None, threshold=0.5, min_dup_chars=60,
                protect_tokens=0, policy_fn=None, two_tier=True):
        return real(transcripts, task, threshold, min_dup_chars,
                    protect_tokens, stub_policy, two_tier=two_tier)
    cli.squeeze_fleet = wrapped
    try:
        argv = ["fleet", pa, pb, "--names", "a,b", "-o", outp,
                "--protect-prefix", "100"]
        buf = io.StringIO()
        with redirect_stdout(buf):
            old = sys.argv
            sys.argv = ["cli"] + argv
            try:
                cli.main()
            finally:
                sys.argv = old
        saved = json.load(open(outp))
        assert saved["transcripts"]["a"][0]["content"] == \
            t["a"][0]["content"]
        assert saved["report"]["agents"]["a"]["protected_tokens"] > 0
        print("test_fleet_cli_protect: PASS")
    finally:
        cli.squeeze_fleet = real


def test_fleet_cli_single_tier():
    d = tempfile.mkdtemp()
    pa = os.path.join(d, "a.json")
    json.dump({"messages": make_transcripts()["a"]}, open(pa, "w"))
    outp = os.path.join(d, "out.json")
    real = cli.squeeze_fleet
    seen = {}

    def spy(transcripts, task=None, threshold=0.5, min_dup_chars=60,
            protect_tokens=0, policy_fn=None, two_tier=True):
        seen["two_tier"] = two_tier
        return real(transcripts, task, threshold, min_dup_chars,
                    protect_tokens, stub_policy, two_tier=two_tier)
    cli.squeeze_fleet = spy

    def run_cli(extra):
        old = sys.argv
        sys.argv = ["cli", "fleet", pa, "--names", "a", "-o", outp,
                    "--protect-prefix", "100"] + extra
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                cli.main()
        finally:
            sys.argv = old
    try:
        run_cli([])
        assert seen.get("two_tier") is True, seen
        run_cli(["--single-tier"])
        assert seen.get("two_tier") is False, seen
        print("test_fleet_cli_single_tier: PASS")
    finally:
        cli.squeeze_fleet = real


def test_fleet_server_protect():
    t = make_transcripts()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        import agent_squeeze.fleet as fleet_mod
        import agent_squeeze.server as server_mod
        real = server_mod.squeeze_fleet

        def wrapped(transcripts, task=None, threshold=0.5, min_dup_chars=60,
                    protect_tokens=0, policy_fn=None, **kw):
            return fleet_mod.squeeze_fleet(transcripts, task, threshold,
                                           min_dup_chars, protect_tokens,
                                           stub_policy, **kw)
        server_mod.squeeze_fleet = wrapped
        try:
            url = f"http://127.0.0.1:{srv.server_port}/v1/squeeze-fleet"
            body = json.dumps({"transcripts": t, "protect_tokens": 100,
                               "task": "shared objective"}).encode()
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type":
                                                  "application/json"})
            with urllib.request.urlopen(req) as r:
                payload = json.load(r)
            assert payload["transcripts"]["a"][0]["content"] == \
                t["a"][0]["content"]
            assert payload["report"]["agents"]["b"]["protected_tokens"] > 0
            print("test_fleet_server_protect: PASS")
        finally:
            server_mod.squeeze_fleet = real
    finally:
        srv.shutdown()


if __name__ == "__main__":
    test_fleet_protect_library()
    test_fleet_protect_default_unchanged()
    test_fleet_cli_protect()
    test_fleet_cli_single_tier()
    test_fleet_server_protect()
    print("ALL FLEET TESTS PASS")
