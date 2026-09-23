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
    POST /v1/admit          {"name": "bash", "text": "...", "task": "..."} ->
                           {"decision": ..., "admitted_text": ..., "ref": ...,
                            "held_chars": ..., "reduction_pct": ...}
                           gate ONE tool result before it enters context.
    POST /v1/admit-batch    {"results": [{"name": ..., "text": ...}], "task": "..."} ->
                           {"admissions": [...], "stats": {...}}
    POST /v1/readmit        {"ref": "⟦held:bash/0003⟧"} ->
                           {"ref": ..., "text": ...}  (byte-identical payload)
    POST /v1/readmit-if-mentioned {"text": "agent follow-up ..."} ->
                           {"found": {"⟦held:bash/0003⟧": "..."}}  scan a later
                           agent message for hold refs and return the payloads.
    Hold refs persist in ~/.agent_squeeze/holds.json (or AGENT_SQUEEZE_HOLD_DIR)
    so they resolve across requests and restarts.

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
from .admit import admit_tool_result, admit_session, PersistentHoldStore

SERVICE_TOKEN = os.environ.get("AGENT_SQUEEZE_TOKEN")


def _hold_store():
    # built per request so tests can redirect via AGENT_SQUEEZE_HOLD_DIR
    return PersistentHoldStore()


def _admission_json(name, adm):
    return {"name": name, "decision": adm.decision, "admitted_text": adm.text,
            "ref": adm.ref, "held_chars": adm.held_chars}


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
            if self.path == "/v1/admit":
                store = _hold_store()
                name = data.get("name") or "tool"
                text = data.get("text") or ""
                adm, _ = admit_tool_result(name, text, data.get("task") or "",
                                          store=store)
                pct = round(100 * (1 - len(adm.text) / len(text)), 2) \
                    if text else 0.0
                payload = _admission_json(name, adm)
                payload["reduction_pct"] = pct
                return self._send(200, payload)
            if self.path == "/v1/admit-batch":
                store = _hold_store()
                results = data.get("results") or []
                items = [{"name": r.get("name") or "tool",
                          "text": r.get("text") or ""}
                         for r in results]
                admissions, stats = admit_session(
                    items, data.get("task") or "", store=store)
                return self._send(
                    200, {"admissions": [
                        _admission_json(i["name"], a)
                        for i, a in zip(items, admissions)], "stats": stats})
            if self.path == "/v1/readmit":
                store = _hold_store()
                ref = data.get("ref")
                try:
                    text = store.readmit(ref)
                except KeyError:
                    return self._send(404, {"error": "unknown ref"})
                return self._send(200, {"ref": ref, "text": text})
            if self.path == "/v1/readmit-if-mentioned":
                store = _hold_store()
                found = store.readmit_if_mentioned(data.get("text") or "")
                return self._send(200, {"found": found})
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
          "POST /v1/squeeze-cache-aware, POST /v1/squeeze-fleet, "
          "POST /v1/admit, POST /v1/admit-batch, POST /v1/readmit, "
          "POST /v1/readmit-if-mentioned")
    server.serve_forever()


if __name__ == "__main__":
    main()
