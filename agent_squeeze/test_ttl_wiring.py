"""Wiring tests: CLI `squeeze-ttl` + POST /v1/recommend-ttl.

Run: PYTHONPATH=. python3 agent_squeeze/test_ttl_wiring.py   (from repo root)
Pure arithmetic over the deterministic TTL simulator — no Jev, no paid calls.
"""
import io
import json
import os
import sys
import threading
import urllib.request
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent_squeeze import cli  # noqa: E402
from agent_squeeze.server import Handler  # noqa: E402


def run_ttl_cli(gaps, prefix, dynamic, price, outfile=None):
    args = cli.main.__globals__["argparse"].Namespace(
        cmd="squeeze-ttl", gaps=gaps, prefix=prefix, dynamic=dynamic,
        price=price, output=outfile)
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_ttl(args)
    return buf.getvalue()


def test_cli_recommends_1hour_for_slow_gaps():
    out = run_ttl_cli("600,900,1200", 200000, 2000, 3.0)
    assert "recommended TTL: 1hour" in out, out
    print("test_cli_recommends_1hour_for_slow_gaps OK")


def test_cli_recommends_5min_for_bursts():
    out = run_ttl_cli("30,45,60", 200000, 2000, 3.0)
    assert "recommended TTL: 5min" in out, out
    print("test_cli_recommends_5min_for_bursts OK")


def test_cli_writes_json():
    path = "/tmp/ttl-rec.json"
    out = run_ttl_cli("600,900", 200000, 0, 3.0, outfile=path)
    doc = json.load(open(path))
    assert doc["recommendation"]["recommended"] == "1hour"
    assert doc["verdict"] in out
    os.unlink(path)
    print("test_cli_writes_json OK")


def test_cli_rejects_empty_gaps():
    args = cli.main.__globals__["argparse"].Namespace(
        cmd="squeeze-ttl", gaps=" , ", prefix=1000, dynamic=0, price=3.0,
        output=None)
    try:
        cli.cmd_ttl(args)
    except SystemExit as e:
        assert "gaps" in str(e), e
        print("test_cli_rejects_empty_gaps OK")
        return
    raise AssertionError("expected SystemExit for empty --gaps")


def _server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def post(port, path, body):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return r.status, json.load(r)


def test_server_recommend_ttl():
    srv, port = _server()
    try:
        status, doc = post(port, "/v1/recommend-ttl", {
            "turn_gaps_sec": [600, 900], "prefix_tokens": 200000,
            "dynamic_tokens": 2000, "base_per_mtok": 3.0})
        assert status == 200, doc
        assert doc["recommended"] == "1hour", doc
        assert doc["saving_pct"] > 0
        status, doc = post(port, "/v1/recommend-ttl", {
            "turn_gaps_sec": [30, 45], "prefix_tokens": 200000,
            "dynamic_tokens": 2000, "base_per_mtok": 3.0})
        assert doc["recommended"] == "5min", doc
    finally:
        srv.shutdown()
    print("test_server_recommend_ttl OK")


def test_server_rejects_missing_gaps():
    import urllib.error
    srv, port = _server()
    try:
        try:
            post(port, "/v1/recommend-ttl", {"prefix_tokens": 1})
        except urllib.error.HTTPError as e:
            assert e.code == 400
            print("test_server_rejects_missing_gaps OK")
            return
        raise AssertionError("expected 400 for missing gaps")
    finally:
        srv.shutdown()


if __name__ == "__main__":
    for name in sorted(k for k, v in list(globals().items())
                       if k.startswith("test_") and callable(v)):
        globals()[name]()
    print("ALL TTL-WIRING TESTS PASSED")
