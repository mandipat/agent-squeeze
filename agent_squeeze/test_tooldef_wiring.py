"""Wiring tests: /v1/prune-tools + /v1/readmit-tool (HTTP) and the two new
MCP tools (prune_tool_definitions, readmit_tool).

Run: python3 test_tooldef_wiring.py   (from the repo root)
Deterministic free policy only — no Jev, no paid calls. Hold persistence is
redirected to a temp dir via AGENT_SQUEEZE_HOLD_DIR.
"""
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent_squeeze.server import Handler  # noqa: E402
from agent_squeeze import mcp_server  # noqa: E402
from agent_squeeze.tooldef import tool_chars  # noqa: E402

TOOLS_FIXTURE = [
    {"name": "exec", "description": "run a shell command",
     "input_schema": {"type": "object", "properties": {"command": {}}}},
    {"name": "read_file", "description": "read a file from disk",
     "input_schema": {"type": "object", "properties": {"path": {}}}},
    {"name": "grep", "description": "search text in files",
     "input_schema": {"type": "object", "properties": {"pattern": {}}}},
    {"name": "pytest", "description": "run the pytest suite",
     "input_schema": {"type": "object", "properties": {"args": {}}}},
    {"name": "git", "description": "git version control commands",
     "input_schema": {"type": "object", "properties": {"args": {}}}},
    {"name": "gh", "description": "GitHub CLI, pull requests and issues",
     "input_schema": {"type": "object", "properties": {"pr": {}}}},
    {"name": "edit_image", "description": "generate or edit an image",
     "input_schema": {"type": "object", "properties": {"prompt": {}}}},
    {"name": "flight_status", "description": "look up airline flight status",
     "input_schema": {"type": "object", "properties": {"flight": {}}}},
]

TASK = "Debug the failing pytest suite, fix the bug, and open a PR"


def post(port, path, body):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_server_prune_tools():
    srv = None
    try:
        srv = _serve()
        resp = post(srv.server_port, "/v1/prune-tools",
                    {"tools": TOOLS_FIXTURE, "task": TASK, "called": ["exec"]})
        kept_names = {t["name"] for t in resp["tools"]}
        for must in ("exec", "pytest", "git", "gh"):
            assert must in kept_names, f"{must} pruned!"
        assert "edit_image" not in kept_names and \
            "flight_status" not in kept_names, kept_names
        assert resp["stats"]["reduction_pct"] > 0, resp["stats"]
        pruned = [l for l in resp["ledger"] if l["decision"] == "prune"]
        assert all(l.get("ref", "").startswith("⟦held:") for l in pruned), \
            "pruned entries need hold refs"
        assert kept_names == {t["name"] for t in resp["tools"]}
        print(f"PASS server prune-tools: {len(kept_names)}/"
              f"{len(TOOLS_FIXTURE)} kept, "
              f"-{resp['stats']['reduction_pct']}% tokens")
        return resp
    finally:
        if srv:
            srv.shutdown()


def test_server_readmit_tool_roundtrip():
    srv = None
    try:
        srv = _serve()
        resp = post(srv.server_port, "/v1/prune-tools",
                    {"tools": TOOLS_FIXTURE, "task": TASK})
        pruned_name = next(l["name"] for l in resp["ledger"]
                           if l["decision"] == "prune")
        back = post(srv.server_port, "/v1/readmit-tool", {"name": pruned_name})
        expect = next(t for t in TOOLS_FIXTURE if t["name"] == pruned_name)
        assert back["text"] == tool_chars(expect), "not byte-identical"
        # by-ref path too
        ref = next(l["ref"] for l in resp["ledger"]
                   if l["decision"] == "prune")
        by_ref = post(srv.server_port, "/v1/readmit-tool", {"ref": ref})
        assert by_ref["text"] == back["text"]
        # unknown name -> 404
        try:
            post(srv.server_port, "/v1/readmit-tool", {"name": "no_such_tool"})
        except urllib.error.HTTPError as e:
            assert e.code == 404, e.code
        else:
            raise AssertionError("unknown tool did not 404")
        print(f"PASS server readmit-tool: '{pruned_name}' byte-identical, "
              "unknown -> 404")
    finally:
        if srv:
            srv.shutdown()


def test_server_prune_tools_validation():
    srv = None
    try:
        srv = _serve()
        try:
            post(srv.server_port, "/v1/prune-tools", {"task": TASK})
        except urllib.error.HTTPError as e:
            assert e.code == 400, e.code
        else:
            raise AssertionError("missing tools did not 400")
        # empty task -> fail-safe keeps everything
        resp = post(srv.server_port, "/v1/prune-tools",
                    {"tools": TOOLS_FIXTURE, "task": ""})
        assert len(resp["tools"]) == len(TOOLS_FIXTURE), "fail-safe broken"
        print("PASS server prune-tools validation: 400 on bad input, "
              "fail-safe keeps all")
    finally:
        if srv:
            srv.shutdown()


def _mcp_call(name, args):
    # stdio-less: invoke the registered tool fn + MCP text envelope directly
    tool = mcp_server.TOOLS[name]
    payload = tool["fn"](args)
    envelope = mcp_server._mcp_text(payload)
    return json.loads(envelope[0]["text"])


def test_mcp_tools_registered():
    for name in ("prune_tool_definitions", "readmit_tool"):
        assert name in mcp_server.TOOLS, f"{name} not in TOOLS"
        schema = mcp_server.TOOLS[name]["schema"]
        assert schema["type"] == "object" and schema.get("properties"), name
    assert len(mcp_server.TOOLS) == 6, "expect 6 MCP tools now"
    print("PASS mcp: both new tools registered with inputSchemas")


def test_mcp_prune_and_readmit_tool():
    resp = _mcp_call("prune_tool_definitions",
                     {"tools": TOOLS_FIXTURE, "task": TASK, "called": ["exec"]})
    kept = {t["name"] for t in resp["tools"]}
    assert {"exec", "pytest", "gh"} <= kept, kept
    assert "flight_status" not in kept, kept
    pruned_name = next(l["name"] for l in resp["ledger"]
                       if l["decision"] == "prune")
    back = _mcp_call("readmit_tool", {"name": pruned_name})
    expect = next(t for t in TOOLS_FIXTURE if t["name"] == pruned_name)
    assert back["text"] == tool_chars(expect), "not byte-identical"
    try:
        _mcp_call("readmit_tool", {"name": "no_such_tool"})
    except ValueError:
        pass
    else:
        raise AssertionError("unknown tool did not raise")
    print(f"PASS mcp: prune ({len(kept)}/{len(TOOLS_FIXTURE)} kept), "
          f"readmit '{pruned_name}' byte-identical")


def main():
    hold_dir = tempfile.mkdtemp(prefix="asq-holds-")
    os.environ["AGENT_SQUEEZE_HOLD_DIR"] = hold_dir
    tests = [test_server_prune_tools, test_server_readmit_tool_roundtrip,
             test_server_prune_tools_validation, test_mcp_tools_registered,
             test_mcp_prune_and_readmit_tool]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} wiring tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
