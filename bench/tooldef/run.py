"""Synthetic tool-definition pruning benchmark -> needle recall check.

40-tool coding-agent tool list (shell, files, git, gh, browser, images,
messaging, calendar, db...) with real-looking JSON schemas; one task:
"Debug the failing pytest suite in ~/workspace/agent_squeeze and open a PR
with the fix." Needles: the tools a real agent needs for that task.
Deterministic free policy — zero paid calls.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from agent_squeeze.tooldef import (  # noqa: E402
    prune_tool_definitions, readmit_tool, deterministic_policy)
from agent_squeeze.admit import HoldStore  # noqa: E402


def tool(name, desc, props):
    return {"name": name, "description": desc,
            "input_schema": {"type": "object", "properties": props,
                             "required": list(props)[:1] if props else []}}


TASK = ("Debug the failing pytest suite in ~/workspace/agent_squeeze and "
        "open a PR with the fix")

TOOLS = [
    # --- needed needles (8) ---
    tool("exec", "Run a shell command in the repo checkout",
         {"command": {"type": "string"}, "workdir": {"type": "string"}}),
    tool("read_file", "Read a source file from disk",
         {"path": {"type": "string"}, "offset": {"type": "integer"}}),
    tool("write_file", "Write or overwrite a source file",
         {"path": {"type": "string"}, "content": {"type": "string"}}),
    tool("edit_file", "Apply a surgical edit to a file",
         {"path": {"type": "string"}, "old_text": {"type": "string"},
          "new_text": {"type": "string"}}),
    tool("grep", "Search file contents with a regex",
         {"pattern": {"type": "string"}, "path": {"type": "string"}}),
    tool("git", "Run a git command: diff, log, status, branch",
         {"args": {"type": "array"}}),
    tool("gh", "GitHub CLI: open a pull request, check CI status",
         {"subcommand": {"type": "string"}, "title": {"type": "string"}}),
    tool("pytest", "Run the pytest suite with options",
         {"path": {"type": "string"}, "k": {"type": "string"}}),
    # --- noise (32) ---
    tool("generate_image", "Generate an image from a text prompt",
         {"prompt": {"type": "string"}, "size": {"type": "string"}}),
    tool("edit_image", "Edit an existing image with a mask",
         {"image": {"type": "string"}, "prompt": {"type": "string"}}),
    tool("tts", "Turn text into spoken audio",
         {"text": {"type": "string"}, "voice": {"type": "string"}}),
    tool("send_email", "Send an email via Gmail",
         {"to": {"type": "string"}, "subject": {"type": "string"},
          "body": {"type": "string"}}),
    tool("search_email", "Search the user's Gmail inbox",
         {"query": {"type": "string"}, "max_results": {"type": "integer"}}),
    tool("calendar_add", "Create a calendar event",
         {"title": {"type": "string"}, "start": {"type": "string"}}),
    tool("calendar_list", "List upcoming calendar events",
         {"days": {"type": "integer"}}),
    tool("spotify_play", "Play music on Spotify",
         {"query": {"type": "string"}, "device": {"type": "string"}}),
    tool("spotify_playlist", "Create a Spotify playlist",
         {"name": {"type": "string"}, "tracks": {"type": "array"}}),
    tool("browser_navigate", "Navigate a live browser to a URL",
         {"url": {"type": "string"}}),
    tool("browser_click", "Click an element in the live browser",
         {"selector": {"type": "string"}}),
    tool("web_search", "Search the web for current information",
         {"query": {"type": "string"}}),
    tool("db_query", "Run a SQL query against the analytics warehouse",
         {"sql": {"type": "string"}}),
    tool("db_schema", "Describe tables in the analytics warehouse",
         {"table": {"type": "string"}}),
    tool("stripe_charge", "Create a Stripe payment charge",
         {"amount_cents": {"type": "integer"}, "currency": {"type": "string"}}),
    tool("plaid_balance", "Read linked bank account balances",
         {"account_id": {"type": "string"}}),
    tool("post_instagram", "Publish an Instagram post",
         {"image": {"type": "string"}, "caption": {"type": "string"}}),
    tool("read_instagram", "Read Instagram feed and stories",
         {"user": {"type": "string"}}),
    tool("slack_send", "Send a Slack message",
         {"channel": {"type": "string"}, "text": {"type": "string"}}),
    tool("translate", "Translate text between languages",
         {"text": {"type": "string"}, "target": {"type": "string"}}),
    tool("ocr", "Extract text from an image with OCR",
         {"image": {"type": "string"}}),
    tool("weather", "Get the weather forecast for a location",
         {"location": {"type": "string"}}),
    tool("stock_quote", "Get a stock quote and daily change",
         {"ticker": {"type": "string"}}),
    tool("flight_status", "Check a flight's departure and arrival",
         {"flight": {"type": "string"}, "date": {"type": "string"}}),
    tool("order_food", "Order food delivery",
         {"restaurant": {"type": "string"}, "items": {"type": "array"}}),
    tool("book_table", "Book a restaurant table on OpenTable",
         {"restaurant": {"type": "string"}, "party": {"type": "integer"}}),
    tool("schedule_cron", "Schedule a recurring background job",
         {"schedule": {"type": "string"}, "prompt": {"type": "string"}}),
    tool("create_memory", "Store a durable memory about the user",
         {"fact": {"type": "string"}}),
    tool("read_health", "Read Apple HealthKit daily metrics",
         {"metric": {"type": "string"}, "days": {"type": "integer"}}),
    tool("play_podcast", "Generate and play a podcast episode",
         {"topic": {"type": "string"}, "voices": {"type": "array"}}),
    tool("deploy", "Deploy the service to production",
         {"env": {"type": "string"}}),
    tool("rollback", "Roll back the last production deploy",
         {"env": {"type": "string"}}),
    tool("vector_search", "Semantic search over the docs corpus",
         {"query": {"type": "string"}, "top_k": {"type": "integer"}}),
]

NEEDLES = {"exec", "read_file", "write_file", "edit_file",
           "grep", "git", "gh", "pytest"}


def main():
    store = HoldStore()
    kept, ledger, stats = prune_tool_definitions(
        TOOLS, TASK, called=["exec"], store=store)

    kept_names = {t["name"] for t in kept}
    recall = sorted(NEEDLES & kept_names)
    lost = sorted(NEEDLES - kept_names)

    # Byte-identical recall of one pruned tool via ledger ref.
    pruned_entry = next(l for l in ledger if l["decision"] == "prune")
    original = next(t for t in TOOLS if t["name"] == pruned_entry["name"])
    roundtrip = readmit_tool(pruned_entry["name"], store, ledger)
    ok = roundtrip == (original["name"] + "\n" + original["description"] + "\n" +
                       json.dumps(original["input_schema"], sort_keys=True))

    print("task: %s" % TASK)
    print("tools: %d -> %d  tokens %d -> %d (-%s%%)" % (
        stats["tools_before"], stats["tools_after"],
        stats["tokens_before"], stats["tokens_after"],
        stats["reduction_pct"]))
    print("kept:", ", ".join(sorted(kept_names)))
    print("needle recall: %d/%d %s" % (len(recall), len(NEEDLES), recall))
    print("lost needles:", lost or "none")
    print("pruned sample: %s (%s)" % (pruned_entry["name"], pruned_entry["reason"]))
    print("readmit roundtrip byte-identical:", ok)

    assert not lost, "LOST NEEDLES: %s" % lost
    assert ok, "readmit roundtrip mismatch"
    assert stats["tools_after"] < stats["tools_before"]
    print("OK: needle recall intact, savings measured, nothing rewritten.")


if __name__ == "__main__":
    main()
