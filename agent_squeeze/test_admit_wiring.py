"""Wiring tests: CLI `admit` subcommand + /v1/admit, /v1/admit-batch,
/v1/readmit, /v1/readmit-if-mentioned endpoints.

Run: python3 test_admit_wiring.py   (from the repo root)
Deterministic policy only — no Jev, no paid calls. Hold persistence is
redirected to a temp dir via AGENT_SQUEEZE_HOLD_DIR.
"""
import io
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent_squeeze import cli  # noqa: E402
from agent_squeeze import admit as admit_mod  # noqa: E402
from agent_squeeze.server import Handler  # noqa: E402


def stub_policy(text, is_error):
    if is_error:
        return ("keep_full", 1.0)
    if "DROPPABLE" in text:
        return ("notice", 0.9)
    return ("trim", 0.8)


def write_jsonl(rows):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return path


def run_admit_cli(rows, task="run the tests"):
    in_path = write_jsonl(rows)
    out_path = in_path + ".out.json"
    try:
        args = cli.main.__globals__["argparse"].Namespace(
            cmd="admit", input=in_path, output=out_path, task=task,
            needles=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.cmd_admit(args)
        doc = json.load(open(out_path))
        return doc["admissions"], doc["stats"], buf.getvalue()
    finally:
        os.unlink(in_path)
        if os.path.exists(out_path):
            os.unlink(out_path)


def post(port, path, body):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def mk_results():
    return [
        {"name": "poll", "text": "DROPPABLE heartbeat ok\n" * 200},  # boilerplate
        # long unique log, no error markers -> trim
        {"name": "deploy", "text": "".join(
            f"line {i:04d}: step completed, value={i * 7}\n" for i in range(120))},
        {"name": "ls", "text": "a.py\nb.py"},                        # short
    ]


def test_cli_admit():
    real = admit_mod.deterministic_policy
    admit_mod.deterministic_policy = stub_policy
    try:
        admissions, stats, printed = run_admit_cli(mk_results())
        decisions = [a["decision"] for a in admissions]
        assert decisions == ["notice", "trim", "keep_full"], decisions
        assert stats["reduction_pct"] > 50, stats
        assert all(a["ref"] for a in admissions[:2]), "trim/notice need refs"
        assert admissions[2]["ref"] is None, "keep_full holds nothing"
        print(f"PASS cli admit: decisions={decisions}, "
              f"reduction={stats['reduction_pct']}%")
    finally:
        admit_mod.deterministic_policy = real


def test_server_admit_roundtrip():
    real = admit_mod.deterministic_policy
    admit_mod.deterministic_policy = stub_policy
    srv = None
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_port
        payload = "DROPPABLE heartbeat ok\n" * 200
        resp = post(port, "/v1/admit",
                    {"name": "poll", "text": payload, "task": "t"})
        assert resp["decision"] == "notice", resp
        assert resp["reduction_pct"] > 50, resp
        assert resp["ref"] and resp["ref"].startswith("⟦held:"), resp
        # readmit: byte-identical payload
        back = post(port, "/v1/readmit", {"ref": resp["ref"]})
        assert back["text"] == payload, "readmit not byte-identical"
        # readmit-if-mentioned: ref in a later agent message resolves
        found = post(port, "/v1/readmit-if-mentioned",
                     {"text": f"per earlier output {resp['ref']} fix it"})
        assert found["found"].get(resp["ref"]) == payload, found
        print(f"PASS server admit roundtrip: decision=notice, "
              f"reduction={resp['reduction_pct']}%, readmit byte-identical")
    finally:
        admit_mod.deterministic_policy = real
        if srv:
            srv.shutdown()


def test_server_admit_batch():
    real = admit_mod.deterministic_policy
    admit_mod.deterministic_policy = stub_policy
    srv = None
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        resp = post(srv.server_port, "/v1/admit-batch",
                    {"results": mk_results(), "task": "t"})
        assert len(resp["admissions"]) == 3, resp
        assert resp["stats"]["reduction_pct"] > 50, resp["stats"]
        assert resp["stats"]["decisions"] == {
            "keep_full": 1, "trim": 1, "notice": 1, "hold": 0}, resp
        print(f"PASS server admit-batch: "
              f"decisions={resp['stats']['decisions']}, "
              f"reduction={resp['stats']['reduction_pct']}%")
    finally:
        admit_mod.deterministic_policy = real
        if srv:
            srv.shutdown()


def test_readmit_unknown_ref_404():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.server_port}/v1/readmit",
            data=json.dumps({"ref": "⟦held:nope/0001⟧"}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=30)
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404, e.code
            print("PASS server readmit unknown ref -> 404")
    finally:
        srv.shutdown()


def test_hold_persistence_across_restarts():
    tmp = tempfile.mkdtemp()
    p1 = admit_mod.PersistentHoldStore(os.path.join(tmp, "holds.json"))
    payload = "x" * 5000
    ref = p1.hold("bash", payload)
    # "restart": brand-new store object, same file
    p2 = admit_mod.PersistentHoldStore(os.path.join(tmp, "holds.json"))
    assert p2.readmit(ref) == payload, "hold did not survive restart"
    assert p2.held_refs() == [ref]
    print("PASS hold persistence across store restarts")


if __name__ == "__main__":
    hold_dir = tempfile.mkdtemp(prefix="holds-")
    os.environ["AGENT_SQUEEZE_HOLD_DIR"] = hold_dir
    test_cli_admit()
    test_server_admit_roundtrip()
    test_server_admit_batch()
    test_readmit_unknown_ref_404()
    test_hold_persistence_across_restarts()
    print("all admit wiring tests passed")
