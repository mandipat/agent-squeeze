"""Convert a Claude Code stream-json transcript to an agent_squeeze bench input.

Claude Code `-p ... --output-format stream-json --verbose` transcripts store
events, one JSON object per line, with types: system/assistant/user/result.

Mapping to bench input messages:
  - assistant events -> {"role": "assistant", "content": <text>,
                         "tool_calls": [{id, name, arguments}]}
  - user events     -> one message per content block:
        tool_result  -> {"role": "tool", "name", "tool_call_id", "content"}
        text         -> {"role": "user", "content"}
  - system/result   -> skipped (the original -p prompt is not in the
                       transcript, so pass --question explicitly)

Output schema matches bench/inputs/*.json:
  {id, scenario, question, expected_answer_contains, evidence, messages}

Usage:
    python to_bench_input.py task2/transcript.jsonl real_task2 \\
        --scenario "Codebase exploration" \\
        --question "How is the agent_squeeze pipeline structured?" \\
        --evidence "exact substring 1" --evidence "exact substring 2" \\
        --expected "jev" --expected "0.5" \\
        --out ../inputs/real_task2.json

Evidence strings are matched case-insensitively as substrings of the
compressed blob by bench/score.py, so they must appear verbatim in the
transcript.
"""
import argparse
import json
import os


def flatten(content):
    """Turn a content block (str | dict | list) into plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if content.get("type") == "text":
            return content.get("text", "")
        # tool results etc. often carry a "content" payload
        return flatten(content.get("content"))
    if isinstance(content, list):
        return "\n".join(flatten(b) for b in content if flatten(b))
    return str(content)


def convert(events):
    messages = []
    for ev in events:
        etype = ev.get("type")
        if etype == "assistant":
            msg = ev.get("message", {})
            texts, calls = [], []
            for block in msg.get("content", []) or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    texts.append(block["text"])
                elif block.get("type") == "tool_use":
                    calls.append({
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "arguments": block.get("input", {}),
                    })
            m = {"role": "assistant"}
            if texts:
                m["content"] = "\n".join(texts)
            if calls:
                m["tool_calls"] = calls
            messages.append(m)
        elif etype == "user":
            msg = ev.get("message", {})
            blocks = msg.get("content", []) or []
            if not isinstance(blocks, list):
                blocks = [{"type": "text", "text": flatten(blocks)}]
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "tool_result":
                    messages.append({
                        "role": "tool",
                        "name": block.get("name", ""),
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": flatten(block.get("content")),
                    })
                elif btype == "text" and block.get("text"):
                    messages.append({"role": "user", "content": block["text"]})
                elif btype not in ("tool_result",):
                    text = flatten(block)
                    if text:
                        messages.append({"role": "user", "content": text})
    return messages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript", help="stream-json .jsonl file")
    ap.add_argument("id", help="bench input id, e.g. real_task2")
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--question", required=True)
    ap.add_argument("--evidence", action="append", default=[],
                    help="verbatim substring to score for recall; repeatable")
    ap.add_argument("--expected", action="append", default=[],
                    help="expected_answer_contains entries; repeatable")
    ap.add_argument("--out", required=True, help="bench input JSON path")
    args = ap.parse_args()

    with open(args.transcript) as f:
        events = [json.loads(line) for line in f if line.strip()]

    messages = convert(events)
    doc = {
        "id": args.id,
        "scenario": args.scenario,
        "question": args.question,
        "expected_answer_contains": args.expected,
        "evidence": args.evidence,
        "messages": messages,
    }
    chars = len(json.dumps(messages))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(doc, f)
    tool_msgs = sum(1 for m in messages if m.get("role") == "tool")
    print(f"{args.id}: {len(messages)} messages ({tool_msgs} tool), "
          f"~{chars // 4} tokens est -> {args.out}")


if __name__ == "__main__":
    main()
