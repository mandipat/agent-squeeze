"""CLI wiring tests for `prune-tools` / `tool-readmit` (Run 30).

Runs prune_tools end-to-end through real cli.main() argv parsing: JSON in,
JSON out, needles check, hold-store persistence across commands, and the
readmit roundtrip. The hold dir is redirected via AGENT_SQUEEZE_HOLD_DIR so
the real ~/.agent_squeeze/holds.json is never touched.

Deterministic free judge everywhere — zero paid calls, no Jev, OpenRouter
key untouched.
"""
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agent_squeeze import cli  # noqa: E402


def tool(name, desc, props):
    return {"name": name, "description": desc,
            "input_schema": {"type": "object", "properties": props,
                             "required": list(props)[:1] if props else []}}


TASK = "Debug the failing pytest suite in the repo and open a PR with the fix"

TOOLS = [
    tool("exec", "Run a shell command in the repo checkout",
         {"command": {"type": "string"}, "workdir": {"type": "string"}}),
    tool("grep", "Search file contents with a regex",
         {"pattern": {"type": "string"}, "path": {"type": "string"}}),
    tool("git", "Run a git command: diff, log, status, branch",
         {"args": {"type": "array"}}),
    tool("gh", "GitHub CLI: open a pull request, check CI status",
         {"subcommand": {"type": "string"}}),
    tool("generate_image", "Generate an image from a text prompt",
         {"prompt": {"type": "string"}, "size": {"type": "string"}}),
    tool("edit_image", "Edit an existing image with a mask",
         {"image": {"type": "string"}, "mask": {"type": "string"}}),
    tool("calendar_add", "Add a calendar event to the schedule",
         {"title": {"type": "string"}, "time": {"type": "string"}}),
    tool("send_sms", "Send an SMS text message to a phone number",
         {"to": {"type": "string"}, "body": {"type": "string"}}),
]

NEEDLES = ["exec", "grep", "git", "gh"]


def _run_cli(argv):
    old = sys.argv
    sys.argv = ["agent_squeeze"] + argv
    try:
        try:
            cli.main()
        except SystemExit as e:
            return e.code
        return 0
    finally:
        sys.argv = old


def test_cli_prune_tools(tmp=None):
    d = tmp or tempfile.mkdtemp(prefix="asq-cli-tooldef-")
    inp = os.path.join(d, "tools.json")
    out = os.path.join(d, "pruned.json")
    needles = os.path.join(d, "needles.txt")
    json.dump(TOOLS, open(inp, "w"))
    open(needles, "w").write("\n".join(NEEDLES) + "\n")
    code = _run_cli(["prune-tools", inp, "-o", out, "--task", TASK,
                     "--needles", needles])
    assert code == 0, f"exit {code}"
    doc = json.load(open(out))
    kept_names = {t["name"] for t in doc["tools"]}
    for n in NEEDLES:
        assert n in kept_names, f"needle {n} pruned"
    assert "generate_image" not in kept_names, "noise kept"
    stats = doc["stats"]
    assert stats["tools_before"] == len(TOOLS)
    assert stats["tools_after"] == len(doc["tools"])
    assert stats["reduction_pct"] > 0
    assert any(e["decision"] == "prune" for e in doc["ledger"])
    print(f"PASS cli: prune-tools {len(TOOLS)} -> {len(doc['tools'])} tools, "
          f"needles 4/4 kept")
    return d


def test_cli_prune_tools_dict_input():
    d = tempfile.mkdtemp(prefix="asq-cli-tooldef-")
    inp = os.path.join(d, "tools.json")
    out = os.path.join(d, "pruned.json")
    json.dump({"tools": TOOLS}, open(inp, "w"))
    code = _run_cli(["prune-tools", inp, "-o", out, "--task", TASK])
    assert code == 0, f"exit {code}"
    doc = json.load(open(out))
    assert doc["stats"]["tools_before"] == len(TOOLS)
    print("PASS cli: prune-tools accepts {\"tools\": [...]} input")
    return d


def test_cli_prune_tools_needles_exit1():
    d = tempfile.mkdtemp(prefix="asq-cli-tooldef-")
    inp = os.path.join(d, "tools.json")
    out = os.path.join(d, "pruned.json")
    needles = os.path.join(d, "needles.txt")
    json.dump(TOOLS, open(inp, "w"))
    open(needles, "w").write("exec\ngenerate_image\n")  # noise tool gets pruned
    code = _run_cli(["prune-tools", inp, "-o", out, "--task", TASK,
                     "--needles", needles])
    assert code == 1, f"expected exit 1, got {code}"
    print("PASS cli: prune-tools needles failure exits 1")


def test_cli_tool_readmit_roundtrip():
    d = test_cli_prune_tools()  # prune first, populating the hold store
    old_out, sys.stdout = sys.stdout, io.StringIO()
    try:
        code = _run_cli(["tool-readmit", "generate_image"])
        printed = sys.stdout.getvalue()
    finally:
        sys.stdout = old_out
    assert code == 0, f"exit {code}"
    back = json.loads(printed)
    expect = next(t for t in TOOLS if t["name"] == "generate_image")
    assert back == expect, f"not byte-identical: {back}"
    print("PASS cli: tool-readmit generate_image roundtrips the definition")


def test_cli_tool_readmit_unknown():
    d = tempfile.mkdtemp(prefix="asq-cli-tooldef-")
    code = _run_cli(["tool-readmit", "no_such_tool"])
    assert code == 1, f"expected exit 1, got {code}"
    print("PASS cli: tool-readmit unknown name exits 1")


def test_cli_prune_tools_called_sacred():
    d = tempfile.mkdtemp(prefix="asq-cli-tooldef-")
    inp = os.path.join(d, "tools.json")
    out = os.path.join(d, "pruned.json")
    json.dump(TOOLS, open(inp, "w"))
    # send_sms has zero task signal; --called makes it sacred
    code = _run_cli(["prune-tools", inp, "-o", out, "--task", TASK,
                     "--called", "send_sms"])
    assert code == 0, f"exit {code}"
    kept_names = {t["name"] for t in json.load(open(out))["tools"]}
    assert "send_sms" in kept_names, "--called tool was pruned"
    print("PASS cli: prune-tools --called keeps sacred tool")


def main():
    hold_dir = tempfile.mkdtemp(prefix="asq-holds-")
    os.environ["AGENT_SQUEEZE_HOLD_DIR"] = hold_dir
    tests = [test_cli_prune_tools, test_cli_prune_tools_dict_input,
             test_cli_prune_tools_needles_exit1,
             test_cli_tool_readmit_roundtrip, test_cli_tool_readmit_unknown,
             test_cli_prune_tools_called_sacred]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} CLI tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
