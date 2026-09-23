"""Tests for agent_squeeze/tooldef.py (plain asserts; pytest-free runner)."""
import json

from agent_squeeze.tooldef import (
    prune_tool_definitions, deterministic_policy, readmit_tool,
    tool_chars)
from agent_squeeze.admit import HoldStore


def _tool(name, desc, props=None):
    return {"name": name, "description": desc,
            "input_schema": {"type": "object", "properties": props or {}}}


TASK = "Debug the failing pytest suite and open a PR with the fix"


def test_called_tool_is_sacred():
    t = _tool("mystery_tool", "Does something obscure with widgets")
    kept, ledger, _ = prune_tool_definitions([t], TASK, called=["mystery_tool"])
    assert kept and kept[0]["name"] == "mystery_tool"
    assert ledger[0]["decision"] == "keep"
    assert "sacred" in ledger[0]["reason"]


def test_relevant_tools_kept_irrelevant_pruned():
    tools = [_tool("read_file", "Read a source file from disk"),
             _tool("grep", "Search file contents with a regex"),
             _tool("generate_image", "Generate an image from a text prompt")]
    kept, ledger, stats = prune_tool_definitions(tools, TASK, store=HoldStore())
    names = {t["name"] for t in kept}
    assert names == {"read_file", "grep"}, names
    by_name = {l["name"]: l for l in ledger}
    assert by_name["generate_image"]["decision"] == "prune"
    assert "ref" in by_name["generate_image"]
    assert stats["kept"] == 2 and stats["pruned"] == 1
    assert stats["tokens_after"] < stats["tokens_before"]


def test_fail_safe_empty_task_keeps_all():
    tools = [_tool("a", "does x"), _tool("b", "does y")]
    kept, ledger, _ = prune_tool_definitions(tools, "", store=HoldStore())
    assert len(kept) == 2
    assert all(l["decision"] == "keep" for l in ledger)


def test_fail_safe_nonsense_task_keeps_all():
    tools = [_tool("alpha", "handles widgets"), _tool("beta", "handles gizmos")]
    kept, ledger, _ = prune_tool_definitions(
        tools, "xyzzy plugh", store=HoldStore())
    assert len(kept) == 2  # no signal at all -> keep everything


def test_pruned_readmit_byte_identical():
    t = _tool("generate_image", "Generate an image from a text prompt",
              {"prompt": {"type": "string"}})
    signal = _tool("grep", "Search file contents with a regex")  # keeps the fail-safe off
    store = HoldStore()
    kept, ledger, _ = prune_tool_definitions([t, signal], TASK, store=store)
    assert [x["name"] for x in kept] == ["grep"]
    expected = tool_chars(t)
    assert readmit_tool("generate_image", store, ledger) == expected
    assert readmit_tool("generate_image", store) == expected  # no-ledger path


def test_readmit_unknown_name_raises():
    store = HoldStore()
    try:
        readmit_tool("nope", store, [])
    except KeyError:
        return
    raise AssertionError("expected KeyError")


def test_kept_tools_verbatim_and_injectable_policy():
    t = _tool("read_file", "Read a source file from disk",
              {"path": {"type": "string"}})
    calls = []

    def custom(tool, task, called):
        calls.append(tool["name"])
        return ("keep", 0.9, "custom") if tool["name"] == "read_file" \
            else ("prune", 0.1, "custom")

    noise = _tool("noise", "unrelated widget frobnicator")
    kept, ledger, _ = prune_tool_definitions(
        [t, noise], "anything", policy_fn=custom, store=HoldStore())
    assert calls == ["read_file", "noise"]
    assert kept == [t]  # verbatim: same object, never rewritten
    assert json.dumps(kept[0]["input_schema"]) == json.dumps(
        t["input_schema"])
