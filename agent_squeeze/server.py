"""agent-squeeze HTTP service — stdlib only, zero dependencies.

Point your agents at this URL; compression happens automatically:

    export OPENROUTER_API_KEY=sk-or-v1-...
    agent-squeeze-serve --port 8765
    # optional: export AGENT_SQUEEZE_TOKEN=secret  (clients send
    #          Authorization: Bearer secret)

Endpoints:
    GET  /health
    POST /v1/squeeze       {"messages": [...], "task": "..."} ->
                           {"messages": [...], "stats": {...}}
    POST /v1/squeeze-cache-aware  {"messages": [...], "task": "...",
                           "protect_tokens": 1024} ->
                           {"messages": [...], "stats": {...}}
                           first N tokens returned byte-identical so the
                           provider prompt cache keeps hitting.
    POST /v1/squeeze-fleet {"transcripts": {"a": [...], "b": [...]}, "task": "..."} ->
                           {"transcripts": {...}, "report": {...}}

"task" is the agents' objective (not a compression instruction). Omit it and
each agent's task is inferred from its own first user message.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .fleet import squeeze_fleet
from .messages import from_openai, infer_task
from .cache import squeeze_cache_aware
from .squeeze import squeeze_transcript

SERVICE_TOKEN = os.environ.get("AGENT_SQUEEZE_TOKEN")


def _as_messages(value):
    if isinstance(value, dict) and "messages" in value:
        return from_openai(value)
    if isinstance(value, list):
        return from_openai({"messages": value})
    raise ValueError("expected {'messages': [...]} or a bare message list")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        return json.loads(self.rfile.read(n) or b"{}")

    def _authorized(self):
        if not SERVICE_TOKEN:
            return True
        return self.headers.get("Authorization") == f"Bearer {SERVICE_TOKEN}"

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True, "service": "agent-squeeze"})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized():
            return self._send(401, {"error": "unauthorized"})
        try:
            data = self._read_json()
        except Exception:
            return self._send(400, {"error": "invalid JSON"})
        try:
            threshold = float(data.get("threshold", 0.5))
            if self.path == "/v1/squeeze":
                messages = _as_messages(data.get("messages"))
                task = data.get("task") or infer_task(messages)
                out, stats = squeeze_transcript(messages, task, threshold)
                return self._send(200, {"messages": out, "stats": stats})
            if self.path == "/v1/squeeze-cache-aware":
                messages = _as_messages(data.get("messages"))
                task = data.get("task") or infer_task(messages)
                protect = int(data.get("protect_tokens", 1024))
                out, stats = squeeze_cache_aware(messages, task, protect,
                                                 threshold=threshold)
                return self._send(200, {"messages": out, "stats": stats})
            if self.path == "/v1/squeeze-fleet":
                raw = data.get("transcripts") or {}
                transcripts = {k: _as_messages(v) for k, v in raw.items()}
                out, report = squeeze_fleet(
                    transcripts, data.get("task"), threshold)
                return self._send(200, {"transcripts": out, "report": report})
        except RuntimeError as e:  # e.g. OPENROUTER_API_KEY missing
            return self._send(500, {"error": str(e)})
        except Exception as e:  # never leak tracebacks to clients
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        self._send(404, {"error": "not found"})

    def log_message(self, *args):  # quiet by default
        pass


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="agent-squeeze-serve")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("PORT", "8765")))
    args = ap.parse_args()
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("warning: OPENROUTER_API_KEY is not set — squeeze calls will fail")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"agent-squeeze serving on http://{args.host}:{args.port}")
    print("endpoints: GET /health, POST /v1/squeeze, "
          "POST /v1/squeeze-cache-aware, POST /v1/squeeze-fleet")
    server.serve_forever()


if __name__ == "__main__":
    main()
