"""Tests for agent_squeeze/mcp_server.py. Offline: deterministic free policy,
no Jev, no paid calls.
"""
import io
import json
import os
import tempfile

from agent_squeeze.mcp_server import McpServer


def mk_server(hold_dir=None):
    if hold_dir:
        os.environ["AGENT_SQUEEZE_HOLD_DIR"] = hold_dir
    inp, out = io.StringIO(), io.StringIO()
    return McpServer(inp=inp, out=out), inp, out


def call(server, method, params=None, rid=1):
    return server.handle({"jsonrpc": "2.0", "id": rid,
                          "method": method, "params": params or {}})


def test_initialize():
    s, _, _ = mk_server()
    r = call(s, "initialize")
    assert r["result"]["protocolVersion"] == "2024-11-05"
    assert r["result"]["serverInfo"]["name"] == "agent-squeeze"


def test_tools_list():
    s, _, _ = mk_server()
    names = [t["name"] for t in call(s, "tools/list")["result"]["tools"]]
    assert set(names) == {"squeeze_transcript", "admit_tool_result",
                          "readmit", "recommend_ttl",
                          "prune_tool_definitions", "readmit_tool"}
    for t in call(s, "tools/list", rid=2)["result"]["tools"]:
        assert "inputSchema" in t


def test_unknown_tool_errors():
    s, _, _ = mk_server()
    r = call(s, "tools/call", {"name": "nope", "arguments": {}})
    assert r["error"]["code"] == -32602


def mk_transcript():
    return [
        {"role": "system", "content": "you are a coding agent"},
        {"role": "user", "content": "fix the crash"},
        {"role": "assistant", "content": "[tool call: Bash]"},
        {"role": "tool", "name": "Bash", "content": "heartbeat ok\n" * 800},
        {"role": "assistant", "content": "[tool call: Bash]"},
        {"role": "tool", "name": "Bash",
         "content": ("Traceback (most recent call last):\n"
                     "  File \"app.py\", line 42, in main\n"
                     "    run()\n  File \"app.py\", line 17, in run\n"
                     "    raise AssertionError('boom')\n"
                     "AssertionError: boom\n")},
    ]


def test_squeeze_drops_boilerplate_keeps_traceback():
    s, _, _ = mk_server()
    r = call(s, "tools/call", {"name": "squeeze_transcript",
                               "arguments": {"messages": mk_transcript(),
                                             "task": "fix the crash",
                                             "protect_tokens": 10}})
    payload = json.loads(r["result"]["content"][0]["text"])
    assert payload["stats"]["tokens_after"] < payload["stats"]["tokens_before"]
    assert payload["stats"]["cost_usd"] == 0.0  # free policy
    hb = [m for m in payload["messages"]
          if m.get("role") == "tool" and "heartbeat" in m["content"]]
    tb = [m for m in payload["messages"]
          if m.get("role") == "tool" and "AssertionError" in m["content"]]
    assert len(hb) == 1 and "[squeezed:" in hb[0]["content"]
    assert len(tb) == 1 and "Traceback (most recent call last)" in tb[0]["content"]


def test_admit_notice_and_readmit_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        s, _, _ = mk_server(d)
        text = "heartbeat ok\n" * 800
        r = call(s, "tools/call", {"name": "admit_tool_result",
                                   "arguments": {"name": "bash", "text": text}})
        payload = json.loads(r["result"]["content"][0]["text"])
        assert payload["decision"] in ("notice", "trim", "hold")
        assert payload["ref"], "boilerplate must be held"
        r2 = call(s, "tools/call", {"name": "readmit",
                                    "arguments": {"ref": payload["ref"]}},
                  rid=2)
        assert json.loads(r2["result"]["content"][0]["text"])["text"] == text


def test_readmit_unknown_ref_errors():
    with tempfile.TemporaryDirectory() as d:
        s, _, _ = mk_server(d)
        r = call(s, "tools/call", {"name": "readmit",
                                   "arguments": {"ref": "nope"}})
        assert r["error"]["code"] == -32602


def test_recommend_ttl_slow_gaps_pick_1hour():
    s, _, _ = mk_server()
    r = call(s, "tools/call",
             {"name": "recommend_ttl",
              "arguments": {"turn_gaps_sec": [600, 900, 1200]}})
    payload = json.loads(r["result"]["content"][0]["text"])
    assert payload["recommended"] == "1hour"
    assert payload["cost_1hour_usd"] < payload["cost_5min_usd"]
    assert payload["saving_pct"] > 0


def test_recommend_ttl_burst_gaps_pick_5min():
    s, _, _ = mk_server()
    r = call(s, "tools/call",
             {"name": "recommend_ttl",
              "arguments": {"turn_gaps_sec": [30, 45, 60]}})
    payload = json.loads(r["result"]["content"][0]["text"])
    assert payload["recommended"] == "5min"


def test_recommend_ttl_missing_gaps_errors():
    s, _, _ = mk_server()
    r = call(s, "tools/call", {"name": "recommend_ttl", "arguments": {}})
    assert r["error"]["code"] == -32602


def test_parse_error_and_serve_forever():
    s, inp, out = mk_server()
    inp.write('{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n')
    inp.write("not json\n")
    inp.seek(0)
    s.serve_forever()
    lines = [json.loads(l) for l in out.getvalue().strip().split("\n")]
    assert lines[0]["result"] == {}
    assert lines[1]["error"]["code"] == -32700
