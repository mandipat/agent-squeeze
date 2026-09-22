"""Message format + adapters.

Internal format: list of dicts {"role": ..., "name": ..., "content": ...}
  role: "user" | "assistant" | "tool"      (tool = tool result)
  name: tool name for role=="tool" (Read, Bash, Grep, Glob, Task, ...)
"""
import json


def estimate_tokens(text):
    return max(1, len(text) // 4)


def transcript_tokens(messages):
    return sum(estimate_tokens(m.get("content", "")) for m in messages)


def from_openai(doc):
    """Accept {"messages": [...]} with OpenAI-ish roles (tool/function)."""
    out = []
    for m in doc.get("messages", []):
        role = m.get("role")
        if role in ("tool", "function"):
            out.append({"role": "tool", "name": m.get("name", "tool"),
                        "content": str(m.get("content", ""))})
        elif role in ("user", "assistant", "system"):
            out.append({"role": role, "name": "",
                        "content": str(m.get("content", ""))})
    return out


def from_claude_jsonl(path):
    """Best-effort adapter for Claude Code transcripts
    (~/.claude/projects/<proj>/*.jsonl). Each line: {"type": ..., "message":
    {"role": ..., "content": [...]}} with content blocks of type text/tool_use/
    tool_result. Unknown shapes are passed through as text."""
    messages = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = entry.get("message") or {}
            role = msg.get("role", entry.get("type", "user"))
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    btype = block.get("type", "")
                    if btype == "tool_use":
                        messages.append({"role": "assistant", "name": "",
                                         "content": f"[tool call: {block.get('name', '')} "
                                                    f"{json.dumps(block.get('input', {}))[:500]}]"})
                    elif btype == "tool_result":
                        c = block.get("content", "")
                        if isinstance(c, list):
                            c = "\n".join(b.get("text", "") for b in c
                                          if isinstance(b, dict))
                        messages.append({"role": "tool",
                                         "name": block.get("name", "tool"),
                                         "content": str(c)})
                    else:
                        messages.append({"role": role, "name": "",
                                         "content": str(block.get("text", block))})
            elif isinstance(content, str) and content.strip():
                messages.append({"role": role if role in ("user", "assistant") else "user",
                                 "name": "", "content": content})
    return messages


def load_any(path):
    """JSONL -> claude adapter; .json -> openai doc (or {"transcripts": ...})."""
    if path.endswith(".jsonl"):
        return from_claude_jsonl(path)
    doc = json.load(open(path))
    if isinstance(doc, dict) and "transcripts" in doc:
        return {k: from_openai(v) for k, v in doc["transcripts"].items()}
    return from_openai(doc)


def infer_task(messages, max_chars=2000):
    """Autonomous default: the agent's objective is its first user message.
    Never default to a compression-flavored task — that tells the judge the
    wrong thing about what 'needed' means."""
    for m in messages:
        if m.get("role") == "user" and m.get("content", "").strip():
            text = m["content"].strip()
            return text[:max_chars] + ("..." if len(text) > max_chars else "")
    return "complete the agent's task; preserve everything the final answer may depend on"
