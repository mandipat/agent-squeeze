"""Wiring tests: --protect-prefix CLI flag and /v1/squeeze-cache-aware endpoint.

Run: python3 test_cache_wiring.py   (from the repo root)
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
import agent_squeeze.cache as cache_mod  # noqa: E402
from agent_squeeze import cli  # noqa: E402
from agent_squeeze.server import Handler  # noqa: E402


def stub_policy(chunks, task):
    # drop chunks containing the marker word, keep everything else
    return ([0.0 if "DROPPABLE" in c else 1.0 for c in chunks], 0.0)


def mk_messages():
    return [
        {"role": "system", "content": "system prompt here"},
        {"role": "user", "content": "do the thing"},
        {"role": "assistant", "content": "[tool call: Bash]"},
        {"role": "tool", "content": "DROPPABLE heartbeat ok\n" * 200},
        {"role": "assistant", "content": "[tool call: Read]"},
        {"role": "tool", "content": "DROPPABLE heartbeat ok\n" * 200},
        {"role": "assistant", "content": "Traceback shows X"},
        {"role": "tool", "content": "Traceback (most recent call last): ... AssertionError"},
    ]


def write_json(msgs):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump({"messages": msgs}, f)
    return path


def run_squeeze_cli(msgs, protect_prefix, single_tier=False):
    in_path = write_json(msgs)
    out_path = in_path + ".out.json"
    try:
        args = cli.main.__globals__["argparse"].Namespace(
            cmd="squeeze", input=in_path, output=out_path, task=None,
            threshold=0.5, protect_prefix=protect_prefix, needles=None,
            single_tier=single_tier, overlap_chars=0)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.cmd_squeeze(args)
        doc = json.load(open(out_path))
        return doc["messages"], doc["stats"], buf.getvalue()
    finally:
        os.unlink(in_path)
        if os.path.exists(out_path):
            os.unlink(out_path)


def test_cli_cache_aware():
    cache_mod.jev.score_chunks, real = stub_policy, cache_mod.jev.score_chunks
    try:
        msgs = mk_messages()
        out, stats, printed = run_squeeze_cli(msgs, protect_prefix=100)
        assert "protected_tokens" in stats and stats["protected_tokens"] > 0, stats
        assert stats["reduction_pct"] > 0, stats  # tail actually squeezed
        # first 100 tokens byte-identical through json round-trip
        from agent_squeeze.messages import from_openai
        norm = from_openai({"messages": msgs})
        blob_orig = json.dumps(norm[:3], sort_keys=True)
        blob_out = json.dumps(out[:3], sort_keys=True)
        assert blob_orig == blob_out, "protected prefix was rewritten"
        assert "cache-aware" in printed
        print(f"PASS cli --protect-prefix: protected={stats['protected_tokens']} "
              f"tokens, reduction={stats['reduction_pct']}%")
    finally:
        cache_mod.jev.score_chunks = real


def test_cli_off_by_default():
    cache_mod.jev.score_chunks, real = stub_policy, cache_mod.jev.score_chunks
    try:
        msgs = mk_messages()
        out, stats, printed = run_squeeze_cli(msgs, protect_prefix=0)
        assert "protected_tokens" not in stats, stats  # classic path stats
        assert "cache-aware" not in printed
        print(f"PASS cli default path unchanged: reduction={stats['reduction_pct']}%")
    finally:
        cache_mod.jev.score_chunks = real


def test_cli_single_tier_propagates():
    # --single-tier on the --protect-prefix path must reach squeeze_cache_aware
    real = cli.squeeze_cache_aware
    seen = {}

    def spy(messages, task, protect_tokens=1024, **kw):
        seen.update(kw)
        return real(messages, task, protect_tokens=protect_tokens,
                    policy_fn=stub_policy, **kw)
    cli.squeeze_cache_aware = spy
    try:
        msgs = mk_messages()
        run_squeeze_cli(msgs, protect_prefix=100, single_tier=True)
        assert seen.get("two_tier") is False, seen
        run_squeeze_cli(msgs, protect_prefix=100, single_tier=False)
        assert seen.get("two_tier") is True, seen
        print(f"PASS cli --single-tier -> two_tier flag: True/False both wired")
    finally:
        cli.squeeze_cache_aware = real


def test_server_endpoint():
    cache_mod.jev.score_chunks, real = stub_policy, cache_mod.jev.score_chunks
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        msgs = mk_messages()
        body = json.dumps(
            {"messages": msgs, "task": "do the thing",
             "protect_tokens": 100, "threshold": 0.5}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.server_port}/v1/squeeze-cache-aware",
            data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.load(r)
        out, stats = resp["messages"], resp["stats"]
        assert stats["protected_tokens"] > 0 and stats["reduction_pct"] > 0
        from agent_squeeze.messages import from_openai
        norm = from_openai({"messages": msgs})
        blob_orig = json.dumps(norm[:3], sort_keys=True)
        blob_out = json.dumps(out[:3], sort_keys=True)
        assert blob_orig == blob_out, "server rewrote the protected prefix"
        srv.shutdown()
        print(f"PASS server /v1/squeeze-cache-aware: protected="
              f"{stats['protected_tokens']} tokens, reduction={stats['reduction_pct']}%")
    finally:
        cache_mod.jev.score_chunks = real


if __name__ == "__main__":
    test_cli_cache_aware()
    test_cli_off_by_default()
    test_cli_single_tier_propagates()
    test_server_endpoint()
    print("all wiring tests passed")
