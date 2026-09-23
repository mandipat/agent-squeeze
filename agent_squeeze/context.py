"""Turn-structure-aware parsing of Anthropic Messages API JSON.

v1 (bench/pruners/jev_prune.py) sliced tool-result *text* into 6000-char
chunks and judged each in isolation against a static prompt — no notion of
turns, no memory between chunks. This module replaces that with structure:

- {"messages": [{"role": "user"|"assistant",
                 "content": "string" | [blocks]}]}
- blocks: {"type":"text","text":...},
          {"type":"tool_use","id":"toolu_...","name":...,"input":{...}},
          {"type":"tool_result","tool_use_id":"toolu_...","content":...,
           "is_error":bool},
          plus thinking / redacted_thinking.

Pairing: tool_use <-> tool_result are matched by id. One "turn" = a user or
assistant message with its blocks; a tool call + its result is one atomic
unit (in bench-input format the tool_result arrives as a separate
{"role":"tool"} message, so the parser merges it into its assistant turn).

Also accepts agent_squeeze bench inputs
({id,scenario,question,evidence,messages} with roles user/assistant/tool,
assistant.tool_calls=[{id,name,arguments}], tool.tool_call_id) and
normalizes them to the same Turn list. Format is auto-detected.

Never does near-duplicate collapsing: that destroyed needle recall in the
round-2 benchmark (see README). Pairing here is exact id-match only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolPair:
    """One atomic tool_use/tool_result unit, matched by exact id."""
    id: str
    name: str
    input: dict = field(default_factory=dict)
    result: str = ""
    is_error: bool = False
    has_result: bool = False
    use_turn: int = -1      # index into turns[]
    result_turn: int = -1   # index into turns[] (may equal use_turn)
    replacement: str = ""   # deterministic-pass one-liner, if replaced


@dataclass
class Turn:
    index: int
    role: str  # "user" | "assistant"
    text: str = ""           # concatenated text blocks (thinking excluded)
    blocks: list = field(default_factory=list)  # normalized block dicts
    tool_pairs: list = field(default_factory=list)  # ToolPair
    # (message_position, message_dict) in the ORIGINAL messages list, for
    # faithful reassembly of messages_after.
    source: list = field(default_factory=list)
    dropped: bool = False
    summarized: str = ""     # one-line summary if Jev said summarize


# ---------------------------------------------------------------------------
# block helpers
# ---------------------------------------------------------------------------

def _flatten_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if content.get("type") == "text":
            return content.get("text", "")
        return _flatten_content(content.get("content"))
    if isinstance(content, list):
        return "\n".join(_flatten_content(b) for b in content
                         if _flatten_content(b))
    return str(content)


def _norm_block(block: Any) -> dict | None:
    """Normalize one Messages API content block to a plain dict."""
    if isinstance(block, str):
        return {"type": "text", "text": block} if block else None
    if not isinstance(block, dict):
        return None
    btype = block.get("type", "")
    if btype == "text":
        t = block.get("text", "")
        return {"type": "text", "text": t} if t else None
    if btype == "tool_use":
        return {"type": "tool_use",
                "id": block.get("id", ""),
                "name": block.get("name", ""),
                "input": block.get("input", {}) or {}}
    if btype == "tool_result":
        return {"type": "tool_result",
                "tool_use_id": block.get("tool_use_id", ""),
                "content": _flatten_content(block.get("content")),
                "is_error": bool(block.get("is_error", False))}
    if btype in ("thinking", "redacted_thinking"):
        # Internal reasoning: never judged, never emitted. Conclusions live
        # in the assistant's text blocks.
        return {"type": "thinking", "omitted": True}
    # unknown block kinds -> best-effort text
    t = _flatten_content(block)
    return {"type": "text", "text": t} if t else None


def _norm_blocks(content: Any) -> list:
    if content is None:
        return []
    if isinstance(content, (str, dict)):
        content = [content]
    out = []
    for b in content or []:
        nb = _norm_block(b)
        if nb is not None:
            out.append(nb)
    return out


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def _is_bench_format(messages: list) -> bool:
    return any(isinstance(m, dict) and m.get("role") == "tool"
               for m in messages)


def parse_messages_api(messages: list) -> list[Turn]:
    """Parse true Anthropic Messages API messages into turns."""
    turns: list[Turn] = []
    pairs: dict[str, ToolPair] = {}
    for pos, m in enumerate(messages):
        if not isinstance(m, dict):
            continue
        role = m.get("role", "user")
        if role not in ("user", "assistant"):
            role = "user"  # system-ish / unknown -> treat as user-side context
        turn = Turn(index=len(turns), role=role, source=[(pos, m)])
        for b in _norm_blocks(m.get("content")):
            if b["type"] == "thinking":
                turn.blocks.append(b)
                continue
            turn.blocks.append(b)
            if b["type"] == "text":
                turn.text = (turn.text + "\n" + b["text"]).strip()
            elif b["type"] == "tool_use":
                pair = pairs.setdefault(
                    b["id"], ToolPair(id=b["id"], name=b.get("name", ""),
                                     input=b.get("input", {}) or {},
                                     use_turn=turn.index))
                pair.use_turn = turn.index
                turn.tool_pairs.append(pair)
            elif b["type"] == "tool_result":
                pair = pairs.get(b["tool_use_id"])
                if pair is None:  # orphan result: keep as unnamed pair
                    pair = ToolPair(id=b["tool_use_id"], name="",
                                    use_turn=turn.index)
                    pairs[b["tool_use_id"]] = pair
                    turn.tool_pairs.append(pair)
                pair.result = b["content"]
                pair.is_error = b["is_error"]
                pair.has_result = True
                pair.result_turn = turn.index
        turns.append(turn)
    return turns


def parse_bench_input(messages: list) -> list[Turn]:
    """Parse agent_squeeze bench inputs (roles user/assistant/tool).

    An assistant message's tool_calls become tool_use blocks; the consecutive
    {"role":"tool"} messages that follow are merged into the same turn as
    tool_result blocks, so each tool call + result stays one atomic unit.
    """
    turns: list[Turn] = []
    i, n = 0, len(messages)
    while i < n:
        m = messages[i]
        if not isinstance(m, dict):
            i += 1
            continue
        role = m.get("role")
        if role == "tool":
            # Orphan tool message (no preceding assistant): own turn so it
            # is never silently dropped with someone else's turn.
            turn = Turn(index=len(turns), role="assistant",
                        source=[(i, m)])
            _attach_bench_tool_result(turn, m)
            turns.append(turn)
            i += 1
            continue
        if role not in ("user", "assistant"):
            i += 1
            continue
        turn = Turn(index=len(turns), role=role, source=[(i, m)])
        text = m.get("content", "")
        if isinstance(text, str) and text.strip():
            turn.text = text.strip()
            turn.blocks.append({"type": "text", "text": text.strip()})
        pending: dict[str, ToolPair] = {}
        for tc in m.get("tool_calls", []) or []:
            pair = ToolPair(id=tc.get("id", ""), name=tc.get("name", ""),
                            input=tc.get("arguments", {}) or {},
                            use_turn=turn.index)
            pending[pair.id] = pair
            turn.tool_pairs.append(pair)
            turn.blocks.append({"type": "tool_use", "id": pair.id,
                                "name": pair.name, "input": pair.input})
        # Merge consecutive tool messages into this turn.
        j = i + 1
        while j < n and isinstance(messages[j], dict) \
                and messages[j].get("role") == "tool":
            tm = messages[j]
            turn.source.append((j, tm))
            tid = tm.get("tool_call_id", "")
            pair = pending.get(tid)
            if pair is None:
                pair = ToolPair(id=tid, name=tm.get("name", ""),
                                use_turn=turn.index)
                turn.tool_pairs.append(pair)
            pair.result = tm.get("content", "") or ""
            pair.has_result = True
            pair.result_turn = turn.index
            # error flag: bench converter drops is_error; re-detect below
            turn.blocks.append({"type": "tool_result",
                                "tool_use_id": pair.id,
                                "content": pair.result,
                                "is_error": False})
            j += 1
        turns.append(turn)
        i = j
    # bench format lost is_error in conversion -> heuristic re-detection
    for t in turns:
        for p in t.tool_pairs:
            if p.has_result and _looks_like_error(p.result):
                p.is_error = True
    return turns


def _attach_bench_tool_result(turn: Turn, tm: dict) -> None:
    tid = tm.get("tool_call_id", "")
    pair = ToolPair(id=tid, name=tm.get("name", ""), use_turn=turn.index,
                    result=tm.get("content", "") or "", has_result=True,
                    result_turn=turn.index)
    if _looks_like_error(pair.result):
        pair.is_error = True
    turn.tool_pairs.append(pair)
    turn.blocks.append({"type": "tool_result", "tool_use_id": tid,
                        "content": pair.result, "is_error": pair.is_error})


_ERROR_RE = None


def _looks_like_error(text: str) -> bool:
    """Heuristic error detection for tool results (bench format dropped the
    is_error flag in conversion). Mirrors headroom's error-signal idea with a
    plain regex — no Rust dependency here."""
    global _ERROR_RE
    if _ERROR_RE is None:
        import re
        _ERROR_RE = re.compile(
            r"(?i)(traceback \(most recent call last\)|\berror\b|\bfailed\b"
            r"|\bexception\b|\bdenied\b|\btimeout\b|\babort(?:ed|ing)?\b"
            r"|exit (?:code|status) [1-9]|command not found|no such file"
            r"|\bE[0-9]{3,5}\b|fatal:)")
    return bool(text and _ERROR_RE.search(text[:2000]))


def parse(messages: list, fmt: str = "auto") -> list[Turn]:
    """Parse messages (auto-detect bench vs Messages API format)."""
    if fmt == "auto":
        fmt = "bench" if _is_bench_format(messages) else "messages_api"
    if fmt == "bench":
        return parse_bench_input(messages)
    return parse_messages_api(messages)


# ---------------------------------------------------------------------------
# turn utilities
# ---------------------------------------------------------------------------

def task_intent(turns: list[Turn], max_chars: int = 2000) -> str:
    """The task = the first user message (the agent's objective)."""
    for t in turns:
        if t.role == "user" and t.text.strip():
            return t.text.strip()[:max_chars]
    return ""


def iter_tool_units(turns: list[Turn]):
    """Yield every ToolPair exactly once, in turn order."""
    seen = set()
    for t in turns:
        for p in t.tool_pairs:
            if id(p) not in seen:
                seen.add(id(p))
                yield p


def pair_target(pair: ToolPair) -> str:
    """Human target of a tool call: file path, command, or name."""
    inp = pair.input or {}
    for key in ("file_path", "path", "file", "notebook_path"):
        if inp.get(key):
            return str(inp[key])
    cmd = inp.get("command", "")
    if cmd:
        return str(cmd)[:80]
    for key in ("pattern", "query", "prompt", "description"):
        if inp.get(key):
            return str(inp[key])[:80]
    return pair.name


def turn_text_for_judge(turn: Turn) -> str:
    """Compact textual form of a turn for the Jev judge."""
    parts = []
    if turn.text:
        parts.append(turn.text)
    for p in turn.tool_pairs:
        tgt = pair_target(p)
        if p.replacement:
            parts.append(f"[tool {p.name} {tgt}: {p.replacement}]")
        elif p.has_result:
            preview = p.result.strip().split("\n")[0][:200]
            parts.append(f"[tool {p.name} {tgt}"
                         f"{' ERROR' if p.is_error else ''}: {preview}... "
                         f"({len(p.result)} chars total)]")
        else:
            parts.append(f"[tool call {p.name} {tgt}: no result yet]")
    return "\n".join(parts)


def turn_canonical_text(turn: Turn) -> str:
    """Stable text for cache keys (post deterministic pass)."""
    return f"{turn.role}\n{turn_text_for_judge(turn)}"


def turn_cache_key(turn: Turn, prompt_version: str) -> str:
    h = hashlib.sha256()
    h.update(prompt_version.encode())
    h.update(b"\n")
    h.update(turn_canonical_text(turn).encode())
    return h.hexdigest()
