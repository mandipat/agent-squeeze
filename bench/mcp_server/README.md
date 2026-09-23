# agent-squeeze as an MCP server

MCP hosts (Claude Desktop, Claude Code, any MCP client) can call token
compression as tools instead of running the HTTP service:
`agent_squeeze/mcp_server.py` speaks stdio JSON-RPC 2.0, stdlib only.

## Tools

| tool | does |
|---|---|
| `squeeze_transcript` | cache-aware squeeze of a message list; first `protect_tokens` returned byte-identical so provider prompt caches keep hitting; tail is pruned |
| `admit_tool_result` | admit-time gate: judge a tool result *before* it enters context (keep_full / trim / notice / hold) |
| `readmit` | resolve a hold ref (e.g. `⟦held:bash/0003⟧`) back to its byte-identical payload |
| `recommend_ttl` | pick the cheaper Anthropic prompt-cache TTL (5-min vs 1-hour) from observed `turn_gaps_sec`; pure arithmetic, $0 — the decision is not "always 1-hour" (see Run 15: for >1h idle gaps the 5-min write is cheaper again) |

Free deterministic policy by default (boilerplate-detector for squeeze,
`admit.py`'s deterministic heuristic for the admit gate) — **zero network,
zero paid calls**. Set `AGENT_SQUEEZE_MCP_JEV=1` to use real TypeSafe Jev
for `squeeze_transcript` (paid, OpenRouter decisions endpoint).

## Install

Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`
on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "agent-squeeze": {
      "command": "python3",
      "args": ["-m", "agent_squeeze.mcp_server"],
      "cwd": "/path/to/agent_squeeze",
      "env": { "AGENT_SQUEEZE_HOLD_DIR": "~/.agent_squeeze" }
    }
  }
}
```

Or register on Claude Code:

```sh
claude mcp add agent-squeeze -- python3 -m agent_squeeze.mcp_server
```

## Smoke test (offline)

```sh
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize"}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
 | python3 -m agent_squeeze.mcp_server
```

All 7 tests in `agent_squeeze/test_mcp.py` pass with no network access.
