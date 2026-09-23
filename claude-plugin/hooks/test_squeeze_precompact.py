"""Tests for claude-plugin/hooks/squeeze-precompact.py.

Run: python3 test_squeeze_precompact.py   (no service, no paid calls)
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "squeeze-precompact.py")

TRANSCRIPT_LINES = [
    {"type": "user", "message": {"role": "user",
     "content": [{"type": "text", "text": "Fix the NullPointerException in PaymentProcessor"}]}},
    {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "I'll search the codebase."},
        {"type": "tool_use", "id": "t1", "name": "Grep",
         "input": {"pattern": "NullPointerException"}}]}},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1",
         "content": [{"type": "text", "text": "src/Pay.java:42 NPE at line 42"}]}]}},
    {"type": "assistant", "message": {"role": "assistant",
     "content": [{"type": "text", "text": "Found it at Pay.java:42."}]}},
]

CANNED_RESPONSE = {
    "messages": [
        {"role": "user", "name": "",
         "content": "Fix the NullPointerException in PaymentProcessor"},
        {"role": "tool", "name": "Grep",
         "content": "src/Pay.java:42 NPE at line 42"},
    ],
    "stats": {"tokens_before": 500, "tokens_after": 60, "reduction_pct": 88.0,
              "protected_tokens": 40, "chunks_total": 2, "chunks_kept": 2,
              "cost_usd": 0.0001},
}

RECEIVED = {}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        assert self.path == "/v1/squeeze-cache-aware", self.path
        body = self.rfile.read(int(self.headers["Content-Length"]))
        RECEIVED["payload"] = json.loads(body)
        resp = json.dumps(CANNED_RESPONSE).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, *args):
        pass


def _run_hook(event, url):
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl",
                                     delete=False) as f:
        for line in TRANSCRIPT_LINES:
            f.write(json.dumps(line) + "\n")
        tpath = f.name
    event = dict(event, transcript_path=tpath)
    env = dict(os.environ, AGENT_SQUEEZE_URL=url,
               AGENT_SQUEEZE_TIMEOUT="3")
    env.pop("AGENT_SQUEEZE_TOKEN", None)
    proc = subprocess.run([sys.executable, HOOK], input=json.dumps(event),
                          capture_output=True, text=True, env=env,
                          timeout=20)
    os.unlink(tpath)
    return proc


def test_success_injects_digest():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        proc = _run_hook(
            {"session_id": "s1", "hook_event_name": "PreCompact",
             "trigger": "auto", "cwd": "/tmp"},
            f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "PreCompact"
    assert "Fix the NullPointerException" in ctx          # original goal
    assert "Pay.java:42" in ctx                          # kept evidence
    assert "88.0" in ctx                                 # stats
    assert "cache-safe" in ctx
    # the hook asked for cache-aware mode with a protected prefix
    assert RECEIVED["payload"]["protect_tokens"] == 4096
    assert RECEIVED["payload"]["task"].startswith("Fix the")


def test_service_down_is_silent():
    proc = _run_hook({"session_id": "s2", "hook_event_name": "PreCompact",
                      "trigger": "manual", "cwd": "/tmp"},
                     "http://127.0.0.1:1")  # nothing listening
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "", f"expected silence, got: {proc.stdout}"


def test_missing_transcript_is_silent():
    event = json.dumps({"session_id": "s3", "transcript_path": "/nonexistent",
                        "hook_event_name": "PreCompact", "trigger": "auto"})
    env = dict(os.environ, AGENT_SQUEEZE_URL="http://127.0.0.1:1",
               AGENT_SQUEEZE_TIMEOUT="3")
    proc = subprocess.run([sys.executable, HOOK], input=event,
                          capture_output=True, text=True, env=env,
                          timeout=20)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


if __name__ == "__main__":
    for name, fn in sorted({k: v for k, v in globals().items()
                            if k.startswith("test_")}.items()):
        fn()
        print(f"PASS {name}")
    print("all hook tests passed")
