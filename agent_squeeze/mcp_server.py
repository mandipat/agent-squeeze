"""agent-squeeze as an MCP server — stdio JSON-RPC 2.0, stdlib only.

Why: agents that speak MCP (Claude Desktop, Claude Code, any MCP host) can
call token compression as tools instead of hitting the HTTP service.
Three tools:

  squeeze_transcript — cache-aware squeeze of a message list. The protected
                       prefix is returned byte-identical so provider prompt
                       caches keep hitting; only the tail is pruned.
  admit_tool_result  — admit-time gate: judge one tool result *before* it
                       enters context (verbatim head+tail excerpt, boilerplate
                       notice, or hold-off-context ref). Nothing is ever lost:
                       held payloads round-trip byte-identically via readmit.
  readmit            — resolve a hold ref (e.g. "⟦held:bash/0003⟧") back to
                       the byte-identical payload.

Default policy is deterministic and free (no network): the squeeze tool
drops chunks that look like boilerplate (low unique-line ratio, mirroring
the admit gate's heuristic) and keeps everything else; the admit tool uses
admit.py's deterministic heuristic. Set AGENT_SQUEEZE_MCP_JEV=1 to use real
TypeSafe Jev for squeeze_transcript (paid — OpenRouter decisions endpoint).

Framing: newline-delimited JSON on stdin/stdout (MCP stdio transport).
Logs go to stderr; stdout carries only JSON-RPC messages.
"""
import io
import json
import os
import sys

from .admit import admit_tool_result, PersistentHoldStore
from .cache import squeeze_cache_aware
from .messages import from_openai, infer_task
from . import jev

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "agent-squeeze", "version": "0.1.0"}

BOILERPLATE_LINE_RATIO = 0.35  # unique/nonblank line ratio below this = boilerplate


def _free_squeeze_policy(chunks, task):
    """Deterministic stub: drop boilerplate chunks, keep the rest.

    Mirrors the admit gate's boilerplate heuristic at chunk granularity.
    Returns (probs, 0.0 cost). No network, no paid calls.
    """
    probs = []
    for c in chunks:
        lines = [ln.strip() for ln in c.splitlines() if ln.strip()]
        ratio = len(set(lines)) / max(1, len(lines))
        probs.append(0.1 if lines and ratio < BOILERPLATE_LINE_RATIO else 1.0)
    return probs, 0.0


def _squeeze_policy():
    if os.environ.get("AGENT_SQUEEZE_MCP_JEV") == "1":
        return jev.score_chunks
    return _free_squeeze_policy


def _tool_squeeze(args):
    raw = args.get("messages") or []
    messages = from_openai({"messages": raw})
    if not messages:  # non-OpenAI shapes: keep role/content verbatim
        messages = [{"role": m.get("role", "user"), "name": m.get("name", ""),
                     "content": str(m.get("content", ""))}
                    for m in raw if isinstance(m, dict)]
    task = args.get("task") or infer_task(messages)
    threshold = float(args.get("threshold", 0.5))
    protect = int(args.get("protect_tokens", 1024))
    out, stats = squeeze_cache_aware(messages, task, protect,
                                    policy_fn=_squeeze_policy(),
                                    threshold=threshold)
    return {"messages": out, "stats": stats}


def _tool_admit(args):
    store = PersistentHoldStore()
    name = args.get("name") or "tool"
    text = args.get("text") or ""
    task = args.get("task") or ""
    adm, _ = admit_tool_result(name, text, task, store=store)
    pct = round(100 * (1 - len(adm.text) / len(text)), 2) if text else 0.0
    return {"decision": adm.decision, "admitted_text": adm.text,
            "ref": adm.ref, "held_chars": adm.held_chars,
            "reduction_pct": pct}


def _tool_readmit(args):
    ref = args.get("ref")
    try:
        text = PersistentHoldStore().readmit(ref)
    except KeyError:
        raise ValueError(f"unknown ref: {ref}")
    return {"ref": ref, "text": text}


TOOLS = {
    "squeeze_transcript": {
        "fn": _tool_squeeze,
        "description": ("Cache-aware token compression of an agent transcript. "
                        "The first `protect_tokens` are returned byte-identical "
                        "so provider prompt caches keep hitting; only the tail "
                        "is pruned. Deterministic free policy by default; set "
                        "AGENT_SQUEEZE_MCP_JEV=1 for real Jev."),
        "schema": {
            "type": "object",
            "properties": {
                "messages": {"type": "array",
                             "description": "chat messages [{role, content}]"},
                "task": {"type": "string",
                         "description": "agent objective (optional)"},
                "threshold": {"type": "number",
                              "description": "keep cutoff, default 0.5"},
                "protect_tokens": {"type": "integer",
                                   "description": "prefix tokens kept "
                                                  "byte-identical, default 1024"},
            },
            "required": ["messages"],
        },
    },
    "admit_tool_result": {
        "fn": _tool_admit,
        "description": ("Admit-time gate: judge a tool result BEFORE it enters "
                        "context. Returns keep_full/trim/notice/hold + the "
                        "admitted text. Held payloads resolve via readmit."),
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "tool name, e.g. 'bash'"},
                "text": {"type": "string",
                         "description": "raw tool result text"},
                "task": {"type": "string",
                         "description": "agent objective (optional)"},
            },
            "required": ["name", "text"],
        },
    },
    "readmit": {
        "fn": _tool_readmit,
        "description": ("Resolve a hold ref (e.g. '⟦held:bash/0003⟧') back to "
                        "its byte-identical payload."),
        "schema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "hold ref"},
            },
            "required": ["ref"],
        },
    },
}


def _mcp_text(payload):
    return [{"type": "text", "text": json.dumps(payload)}]


def _respond(rid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": rid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    return msg


def _err(code, message):
    return {"code": code, "message": message}


class McpServer:
    """Line-delimited JSON-RPC MCP server. `inp`/`out` are text streams."""

    def __init__(self, inp=None, out=None):
        self.inp = inp or sys.stdin
        self.out = out or sys.stdout

    def _write(self, msg):
        self.out.write(json.dumps(msg) + "\n")
        self.out.flush()

    def handle(self, req):
        method = req.get("method", "")
        rid = req.get("id")
        params = req.get("params") or {}
        if method == "initialize":
            return _respond(rid, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO})
        if method in ("notifications/initialized", "ping"):
            return _respond(rid, {})
        if method == "tools/list":
            return _respond(rid, {"tools": [
                {"name": n, "description": t["description"],
                 "inputSchema": t["schema"]} for n, t in TOOLS.items()]})
        if method == "tools/call":
            name = params.get("name")
            tool = TOOLS.get(name)
            if tool is None:
                return _respond(rid, error=_err(-32602,
                                               f"unknown tool: {name}"))
            try:
                payload = tool["fn"](params.get("arguments") or {})
            except ValueError as e:
                return _respond(rid, error=_err(-32602, str(e)))
            except Exception as e:  # never leak tracebacks to the host
                return _respond(rid,
                                error=_err(-32603, f"{type(e).__name__}: {e}"))
            return _respond(rid, {"content": _mcp_text(payload)})
        return _respond(rid, error=_err(-32601, f"unknown method: {method}"))

    def serve_forever(self):
        for line in self.inp:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                self._write(_respond(None, error=_err(-32700, "parse error")))
                continue
            try:
                resp = self.handle(req)
            except Exception as e:
                resp = _respond(req.get("id"),
                               error=_err(-32603, f"{type(e).__name__}: {e}"))
            if resp.get("id") is not None or "error" in resp:
                self._write(resp)


def main():
    # binary-safe: read text lines from stdin buffer
    inp = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8",
                           errors="replace")
    McpServer(inp=inp).serve_forever()


if __name__ == "__main__":
    main()
