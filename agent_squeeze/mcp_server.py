"""agent-squeeze as an MCP server — stdio JSON-RPC 2.0, stdlib only.

Why: agents that speak MCP (Claude Desktop, Claude Code, any MCP host) can
call token compression as tools instead of hitting the HTTP service.
Six tools:

  squeeze_transcript — cache-aware squeeze of a message list. The protected
                       prefix is returned byte-identical so provider prompt
                       caches keep hitting; only the tail is pruned.
  admit_tool_result  — admit-time gate: judge one tool result *before* it
                       enters context (verbatim head+tail excerpt, boilerplate
                       notice, or hold-off-context ref). Nothing is ever lost:
                       held payloads round-trip byte-identically via readmit.
  readmit            — resolve a hold ref (e.g. "⟦held:bash/0003⟧") back to
                       the byte-identical payload.
  recommend_ttl        — pick the cheaper prompt-cache TTL (5-min vs 1-hour)
                       from observed inter-turn gaps. Pure arithmetic, $0.
  prune_tool_definitions — prune the tool list to the task-relevant subset
                       before a session (coding agents pay for 200 irrelevant
                       schemas every turn). Pruned definitions stay held
                       off-context and recall byte-identically via
                       readmit_tool. Recently-called tools are sacred;
                       empty/no-signal tasks keep everything (fail-safe).
  readmit_tool       — recall a pruned tool definition by name (or hold ref)
                       byte-identically when the agent needs it mid-session.

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
from .ttl import recommend_ttl
from .tooldef import prune_tool_definitions, readmit_tool, readmit_by_ref

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
    two_tier = not args.get("single_tier", False)
    overlap = int(args.get("overlap_chars", 0))
    out, stats = squeeze_cache_aware(messages, task, protect,
                                    policy_fn=_squeeze_policy(),
                                    threshold=threshold,
                                    two_tier=two_tier,
                                    overlap_chars=overlap)
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


def _tool_prune_tools(args):
    tools = args.get("tools")
    if not isinstance(tools, list):
        raise ValueError("tools (list of tool definitions) is required")
    kept, ledger, stats = prune_tool_definitions(
        tools, args.get("task") or "", called=args.get("called") or [],
        store=PersistentHoldStore())
    return {"tools": kept, "ledger": ledger, "stats": stats}


def _tool_readmit_tool(args):
    store = PersistentHoldStore()
    try:
        if args.get("ref"):
            return {"name": args.get("name"),
                    "text": readmit_by_ref(args["ref"], store)}
        name = args.get("name")
        return {"name": name, "text": readmit_tool(name, store)}
    except KeyError:
        raise ValueError(f"unknown tool: {args.get('name') or args.get('ref')}")


def _tool_ttl(args):
    gaps = args.get("turn_gaps_sec") or args.get("gaps")
    if not isinstance(gaps, list) or not gaps:
        raise ValueError("turn_gaps_sec (list of seconds) is required")
    try:
        gaps = [float(g) for g in gaps]
    except (TypeError, ValueError):
        raise ValueError("turn_gaps_sec must be a list of numbers")
    prefix = int(args.get("prefix_tokens", 200_000))
    dynamic = int(args.get("dynamic_tokens", 2_000))
    base = float(args.get("base_per_mtok", 3.0))
    rec = recommend_ttl(gaps, prefix, dynamic, base)
    rec["hit_rate_5min"] = round(rec["hit_rate_5min"], 3)
    rec["hit_rate_1hour"] = round(rec["hit_rate_1hour"], 3)
    rec["cost_5min_usd"] = round(rec["cost_5min_usd"], 4)
    rec["cost_1hour_usd"] = round(rec["cost_1hour_usd"], 4)
    return rec


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
                "single_tier": {"type": "boolean",
                                "description": "disable two-tier chunking "
                                             "(uniform 6000-char chunks; "
                                             "default False = finer "
                                             "1500-char chunks on "
                                             "error-dense tool results)"},
                "overlap_chars": {"type": "integer",
                                  "description": "overlap window (chars) on "
                                                 "hard-split chunks so "
                                                 "fragment-blind judges see "
                                                 "boundary-straddling evidence "
                                                 "whole; 100 recommended, "
                                                 "default 0"},
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
    "recommend_ttl": {
        "fn": _tool_ttl,
        "description": ("Pick the cheaper Anthropic prompt-cache TTL "
                        "(5-min vs 1-hour) for a session from its observed "
                        "inter-turn gaps. Pure arithmetic, no network, $0."),
        "schema": {
            "type": "object",
            "properties": {
                "turn_gaps_sec": {"type": "array",
                                  "description": "observed inter-turn gaps in "
                                                 "seconds"},
                "prefix_tokens": {"type": "integer",
                                  "description": "protected prefix size, "
                                                 "default 200000"},
                "dynamic_tokens": {"type": "integer",
                                   "description": "per-turn dynamic tail, "
                                                  "default 2000"},
                "base_per_mtok": {"type": "number",
                                  "description": "base $/MTok, default 3.0"},
            },
            "required": ["turn_gaps_sec"],
        },
    },
    "prune_tool_definitions": {
        "fn": _tool_prune_tools,
        "description": ("Prune an agent's tool list to the task-relevant "
                        "subset (recently-called tools are sacred; no-signal "
                        "tasks keep everything). Pruned definitions are held "
                        "off-context; recall them byte-identically with "
                        "readmit_tool."),
        "schema": {
            "type": "object",
            "properties": {
                "tools": {"type": "array",
                          "description": "tool definitions "
                                         "[{name, description, input_schema}]"},
                "task": {"type": "string",
                         "description": "agent objective"},
                "called": {"type": "array",
                           "description": "tool names already used this "
                                          "session (sacred, never pruned)"},
            },
            "required": ["tools"],
        },
    },
    "readmit_tool": {
        "fn": _tool_readmit_tool,
        "description": ("Recall a pruned tool definition byte-identically, "
                        "by tool name (or hold ref)."),
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "tool name, e.g. 'gh'"},
                "ref": {"type": "string",
                        "description": "hold ref from the prune ledger "
                                       "(alternative to name)"},
            },
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
