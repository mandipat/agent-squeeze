"""Wiring tests: --overlap-chars CLI flag, server overlap/single_tier keys,
MCP squeeze_transcript schema additions, and an end-to-end needle rescue
through the CLI path.

Run: PYTHONPATH=. python3 agent_squeeze/test_overlap_wiring.py
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
from agent_squeeze import cli  # noqa: E402
from agent_squeeze import fleet as fleet_mod  # noqa: E402
from agent_squeeze import server as server_mod  # noqa: E402
from agent_squeeze import mcp_server  # noqa: E402

NEEDLE = "OVERLAP_RESCUE_7QK2"


def stub_policy(chunks, task):
    # fragment-blind: keep a chunk only if the full needle is in it
    return ([1.0 if NEEDLE in c else 0.0 for c in chunks], 0.0)


# NOTE: no import-time patching of the shared jev module here. Two tests in
# this file used to rely on a module-level `jev_mod.score_chunks = stub_policy`
# assignment; it leaked into any other test file loaded into the same process
# (fragment-blind stub poisoning later files' Jev calls). Every path that needs
# the offline stub now passes `policy_fn=stub_policy` explicitly, so importing
# this module is side-effect free for the shared jev module.


def mk_boundary_msgs(needle_offset):
    # Error-dense tool result (triggers 1500-char two-tier chunks) with one
    # 4400-char single line carrying the needle at needle_offset.
    filler = "x" * needle_offset + NEEDLE + "x" * (4400 - needle_offset - len(NEEDLE))
    return [
        {"role": "user", "content": "find the error"},
        {"role": "assistant", "content": "[tool call: Bash]"},
        {"role": "tool", "content": "Traceback (most recent call last):\n"
                                   '  File "app.py", line 9, in main\n'
                                   "AssertionError: boom\n" + filler + "\n"},
    ]


def write_json(doc):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(doc, f)
    return path


def run_cli_main(argv):
    old = sys.argv
    sys.argv = ["cli"] + argv
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            cli.main()
    finally:
        sys.argv = old
    return buf.getvalue()


def test_cli_squeeze_overlap_propagates():
    in_path = write_json({"messages": mk_boundary_msgs(100)})
    out_path = in_path + ".out.json"
    real = cli.squeeze_transcript
    seen = {}

    def spy(messages, task, threshold=0.5, two_tier=True, overlap_chars=0,
            **kw):
        seen.update({"two_tier": two_tier, "overlap_chars": overlap_chars})
        # explicit stub policy: this path hits the real squeeze_transcript,
        # so the stub must be passed in (no module-level jev patch anymore).
        return real(messages, task, threshold, two_tier=two_tier,
                    overlap_chars=overlap_chars, policy_fn=stub_policy)

    cli.squeeze_transcript = spy
    try:
        run_cli_main(["squeeze", in_path, "-o", out_path,
                      "--overlap-chars", "100"])
        assert seen["overlap_chars"] == 100, seen
        assert seen["two_tier"] is True, seen
        run_cli_main(["squeeze", in_path, "-o", out_path,
                      "--single-tier", "--overlap-chars", "50"])
        assert seen["overlap_chars"] == 50, seen
        assert seen["two_tier"] is False, seen
        run_cli_main(["squeeze", in_path, "-o", out_path])  # default off
        assert seen["overlap_chars"] == 0, seen
        print("PASS cli squeeze --overlap-chars propagates (0/50/100) + --single-tier")
    finally:
        cli.squeeze_transcript = real
        os.unlink(in_path)
        if os.path.exists(out_path):
            os.unlink(out_path)


def test_cli_squeeze_cache_aware_overlap_propagates():
    in_path = write_json({"messages": mk_boundary_msgs(100)})
    out_path = in_path + ".out.json"
    real = cli.squeeze_cache_aware
    seen = {}

    def spy(messages, task, protect_tokens=1024, policy_fn=None,
            threshold=0.5, two_tier=True, overlap_chars=0):
        seen.update({"two_tier": two_tier, "overlap_chars": overlap_chars})
        return real(messages, task, protect_tokens=protect_tokens,
                    policy_fn=stub_policy, threshold=threshold,
                    two_tier=two_tier, overlap_chars=overlap_chars)

    cli.squeeze_cache_aware = spy
    try:
        run_cli_main(["squeeze", in_path, "-o", out_path,
                      "--protect-prefix", "100", "--overlap-chars", "100"])
        assert seen["overlap_chars"] == 100, seen
        print("PASS cli squeeze --protect-prefix --overlap-chars propagates")
    finally:
        cli.squeeze_cache_aware = real
        os.unlink(in_path)
        if os.path.exists(out_path):
            os.unlink(out_path)


def test_cli_fleet_overlap_propagates():
    in_path = write_json({"messages": mk_boundary_msgs(100)})
    out_path = in_path + ".out.json"
    real = cli.squeeze_fleet
    seen = {}

    def spy(transcripts, task=None, threshold=0.5, min_dup_chars=60,
            protect_tokens=0, policy_fn=None, two_tier=True,
            overlap_chars=0, **kwargs):
        seen.update({"two_tier": two_tier, "overlap_chars": overlap_chars})
        return real(transcripts, task, threshold, min_dup_chars,
                    protect_tokens, stub_policy, two_tier=two_tier,
                    overlap_chars=overlap_chars, **kwargs)

    cli.squeeze_fleet = spy
    try:
        run_cli_main(["fleet", in_path, "--names", "a", "-o", out_path,
                      "--overlap-chars", "100"])
        assert seen["overlap_chars"] == 100, seen
        assert seen["two_tier"] is True, seen
        print("PASS cli fleet --overlap-chars propagates")
    finally:
        cli.squeeze_fleet = real
        os.unlink(in_path)
        if os.path.exists(out_path):
            os.unlink(out_path)


def test_cli_overlap_rescues_needle_end_to_end():
    # The straddling needle (offset 1495) must be LOST with ov=0 and
    # RESCUED with ov=100 — through the real CLI path, fragment-blind judge.
    import agent_squeeze.squeeze as squeeze_mod
    real_score = squeeze_mod.jev.score_chunks
    squeeze_mod.jev.score_chunks = stub_policy
    try:
        results = {}
        for ov in (0, 100):
            in_path = write_json({"messages": mk_boundary_msgs(1495)})
            out_path = in_path + ".out.json"
            run_cli_main(["squeeze", in_path, "-o", out_path,
                          "--overlap-chars", str(ov)])
            blob = json.dumps(json.load(open(out_path))["messages"])
            results[ov] = NEEDLE in blob
            os.unlink(in_path)
            os.unlink(out_path)
        assert results[0] is False, "ov=0 should lose the straddling needle"
        assert results[100] is True, "ov=100 should rescue it"
        print("PASS cli end-to-end: ov=0 loses, ov=100 rescues the boundary needle")
    finally:
        squeeze_mod.jev.score_chunks = real_score


def post(path, body):
    srv, thread = None, None
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.Handler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.server_port}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    finally:
        if srv:
            srv.shutdown()


def test_server_accepts_overlap_and_single_tier():
    # Wrap the library calls to inject the stub policy (no Jev).
    real_sq, real_ca, real_fl = (server_mod.squeeze_transcript,
                                 server_mod.squeeze_cache_aware,
                                 server_mod.squeeze_fleet)
    seen = {}

    def sq(messages, task, threshold=0.5, two_tier=True, overlap_chars=0):
        seen["sq"] = (two_tier, overlap_chars)
        # explicit stub policy (see note at module top): real_sq would call
        # the paid jev.score_chunks without it.
        return real_sq(messages, task, threshold, two_tier=two_tier,
                       overlap_chars=overlap_chars, policy_fn=stub_policy)

    def ca(messages, task, protect_tokens=1024, policy_fn=None,
           threshold=0.5, two_tier=True, overlap_chars=0):
        seen["ca"] = (two_tier, overlap_chars)
        return real_ca(messages, task, protect_tokens, stub_policy,
                       threshold, two_tier, overlap_chars)

    def fl(transcripts, task=None, threshold=0.5, min_dup_chars=60,
           protect_tokens=0, policy_fn=None, two_tier=True, overlap_chars=0,
           **kw):
        seen["fl"] = (two_tier, overlap_chars)
        return real_fl(transcripts, task, threshold, min_dup_chars,
                       protect_tokens, stub_policy, two_tier, overlap_chars,
                       **kw)

    server_mod.squeeze_transcript, server_mod.squeeze_cache_aware = sq, ca
    server_mod.squeeze_fleet = fl
    try:
        msgs = mk_boundary_msgs(100)
        r = post("/v1/squeeze", {"messages": msgs, "task": "t",
                                 "single_tier": True, "overlap_chars": 100})
        assert "stats" in r and seen["sq"] == (False, 100), seen
        r = post("/v1/squeeze-cache-aware",
                 {"messages": msgs, "task": "t", "protect_tokens": 10,
                  "two_tier": False, "overlap_chars": 50})
        assert "protected_tokens" in r["stats"] and seen["ca"] == (False, 50), seen
        r = post("/v1/squeeze-fleet",
                 {"transcripts": {"a": msgs}, "protect_tokens": 10,
                  "overlap_chars": 100})
        assert "report" in r and seen["fl"] == (True, 100), seen
        print("PASS server /v1/squeeze* accept overlap_chars + single_tier/two_tier")
    finally:
        server_mod.squeeze_transcript = real_sq
        server_mod.squeeze_cache_aware = real_ca
        server_mod.squeeze_fleet = real_fl


def test_mcp_overlap_schema_and_pass_through():
    s = mcp_server.McpServer(inp=io.StringIO(), out=io.StringIO())
    tools = {t["name"]: t for t in
             s.handle({"jsonrpc": "2.0", "id": 1,
                       "method": "tools/list"})["result"]["tools"]}
    props = tools["squeeze_transcript"]["inputSchema"]["properties"]
    assert "single_tier" in props and "overlap_chars" in props, props
    real = mcp_server.squeeze_cache_aware
    seen = {}

    def spy(messages, task, protect, policy_fn=None, threshold=0.5,
            two_tier=True, overlap_chars=0):
        seen.update({"two_tier": two_tier, "overlap_chars": overlap_chars})
        return real(messages, task, protect, policy_fn=policy_fn,
                    threshold=threshold, two_tier=two_tier,
                    overlap_chars=overlap_chars)

    mcp_server.squeeze_cache_aware = spy
    try:
        r = s.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": "squeeze_transcript",
                                 "arguments": {"messages": mk_boundary_msgs(100),
                                               "task": "t",
                                               "protect_tokens": 10,
                                               "single_tier": True,
                                               "overlap_chars": 100}}})
        assert "error" not in r, r
        assert seen == {"two_tier": False, "overlap_chars": 100}, seen
        payload = json.loads(r["result"]["content"][0]["text"])
        assert payload["stats"]["cost_usd"] == 0.0  # still the free policy
        print("PASS mcp squeeze_transcript schema + pass-through "
              "(single_tier/overlap_chars, free policy)")
    finally:
        mcp_server.squeeze_cache_aware = real


if __name__ == "__main__":
    test_cli_squeeze_overlap_propagates()
    test_cli_squeeze_cache_aware_overlap_propagates()
    test_cli_fleet_overlap_propagates()
    test_cli_overlap_rescues_needle_end_to_end()
    test_server_accepts_overlap_and_single_tier()
    test_mcp_overlap_schema_and_pass_through()
    print("all overlap-wiring tests passed")
