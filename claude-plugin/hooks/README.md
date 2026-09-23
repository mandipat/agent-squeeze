# Claude Code compaction-hook recipe: cache-safe compaction

**Problem.** Claude Code's built-in compaction summarizes the transcript.
Summarizing *rewrites* earlier messages, which destroys the provider's prompt
cache: every turn after a compaction re-pays full input price, and the
summary silently drops the original goal and hard constraints (a known,
repeatedly reported failure mode — see anthropics/claude-code#22638).

**Fix.** Run `squeeze-precompact.py` as a `PreCompact` hook. Before
compaction fires, the hook POSTs your transcript to the agent-squeeze
service's `/v1/squeeze-cache-aware` endpoint and injects the result as
`additionalContext`. The post-compaction window then carries:

- the original task (first user message), pinned verbatim;
- an *extractive* keep/drop digest of the tail — evidence kept byte-identical,
  nothing summarized;
- a stats line (tokens before/after, protected-token count).

Because the squeeze used cache-aware mode, the protected prefix (system
prompt + tool definitions + stable history) survives compaction byte-identical
— the next call serves it from prompt cache at cache-read price (0.1x on
Anthropic) instead of full price.

## Install

1. Start the service (it does the Jev keep/drop calls):

   ```bash
   export OPENROUTER_API_KEY=sk-or-v1-...
   agent-squeeze-serve --port 8765
   ```

2. Register the hook in `~/.claude/settings.json` (or project
   `.claude/settings.json`):

   ```json
   {
     "hooks": {
       "PreCompact": [
         {
           "matcher": "auto",
           "hooks": [
             {
               "type": "command",
               "command": "/path/to/agent-squeeze/claude-plugin/hooks/squeeze-precompact.py"
             }
           ]
         },
         {
           "matcher": "manual",
           "hooks": [
             {
               "type": "command",
               "command": "/path/to/agent-squeeze/claude-plugin/hooks/squeeze-precompact.py"
             }
           ]
         }
       ]
     }
   }
   ```

3. Export the service URL in your shell profile:

   ```bash
   export AGENT_SQUEEZE_URL=http://localhost:8765
   ```

## Knobs (env vars)

| var | default | meaning |
|---|---|---|
| `AGENT_SQUEEZE_URL` | *(required)* | service base URL |
| `AGENT_SQUEEZE_TOKEN` | — | bearer token if the server set one |
| `AGENT_SQUEEZE_PROTECT` | `4096` | tokens kept byte-identical (cache-safe prefix) |
| `AGENT_SQUEEZE_DIGEST_CHARS` | `4000` | max chars of kept evidence in the injected digest |
| `AGENT_SQUEEZE_TIMEOUT` | `8` | HTTP timeout (seconds) |

Set `AGENT_SQUEEZE_PROTECT` to cover your system prompt + tool definitions +
stable history — that's the slice the next call serves from cache.

## Safety

- The hook **cannot block compaction** (Claude Code's PreCompact contract);
  it only adds context. Compaction proceeds normally with or without it.
- If the service is down, the transcript is missing, or anything else fails,
  the hook exits 0 silently. A broken hook must never break your session.
- The hook shells nothing and uses stdlib only; transcript content is sent
  only to your configured `AGENT_SQUEEZE_URL`.

## What it does NOT do

It does not replace Claude Code's summary — PreCompact has no transcript
rewrite path. It *supplements* it with a verbatim evidence digest, so the
lossy summary is no longer the only thing the next window sees.

## Test offline (no service, no paid calls)

```bash
python3 claude-plugin/hooks/test_squeeze_precompact.py
```

Spins a fake service, feeds the hook a synthetic transcript, and asserts the
digest injection plus the silent-failure paths.

---

# Admit-time gate in the tool loop: PostToolUse hook

**Problem.** Every tool result lands in the transcript at full size before
anyone judges whether it was worth keeping. Retroactive pruning then has to
guess relevance from cold history — pi-jev-context measured raw Jev dropping
73% of items later needed on real sessions, so its shadow pruning was never
applied. Write-time verbatim trimming on the same project saved 31–53% with
0 key lines lost.

**Fix.** Run `admit-posttooluse.py` as a `PostToolUse` hook. After each tool
result it runs the agent-squeeze admit gate (free deterministic heuristic by
default; set `AGENT_SQUEEZE_ADMIT_JEV=1` for TypeSafe Jev judgments — one
small request per result, watch the OpenRouter cap) and:

1. **Annotates** the judgment to `~/.agent_squeeze/admit-annotations.jsonl`
   (name, length, head/tail hashes, decision, hold ref). The retroactive
   squeezer reads this log via `agent_squeeze.admit.annotation_probs()` and
   uses the recorded decisions as keep/drop priors — fresh write-time
   judgments anchor later pruning instead of the pruner guessing cold.
2. For `trim`/`notice`/`hold` decisions, injects the **admitted text**
   (verbatim excerpt + hold ref) as `additionalContext`, so the model acts
   on the gated excerpt first and knows the full result is recoverable via
   `agent-squeeze admit-readmit <ref>` or `POST /v1/readmit`.
   `keep_full` decisions stay silent — no point re-emitting what's there.

Held payloads persist verbatim in the hold store (`holds.json`), so refs
issued mid-session resolve in later turns.

**Honest limit:** PostToolUse cannot rewrite the tool result already in the
transcript; the raw result stays in history. The hook steers the *model* to
the gated excerpt and records the judgment for the next squeeze pass, which
is where the token savings land (notice-annotated results drop to the
squeezer's keep-one-chunk floor).

## Install

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/agent-squeeze/claude-plugin/hooks/admit-posttooluse.py"
          }
        ]
      }
    ]
  }
}
```

Use a narrower `matcher` (e.g. `"Bash"`) to gate only noisy tools.

## Test offline (no service, no paid calls)

```bash
python3 claude-plugin/hooks/test_admit_posttooluse.py
```

6 tests, all pass: boilerplate → notice + annotation + byte-identical hold
roundtrip; errors and small results stay silent; malformed stdin exits 0;
annotation priors plug into `squeeze_with_policy` and drop a
notice-annotated result's chunks.
