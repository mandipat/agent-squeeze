---
name: squeeze
description: Compress the current transcript (or a fleet of transcripts) via the agent-squeeze service to reclaim context-window tokens. Use when context is getting large, before a compaction, or when handing work to a subagent.
---

# agent-squeeze

You compress transcripts by calling the agent-squeeze service. Nothing you
keep is ever rewritten; compression is extractive and lossless for anything
the judge scores worth keeping.

## Setup

The service URL comes from the environment:

```
AGENT_SQUEEZE_URL   e.g. http://localhost:8765   (required)
AGENT_SQUEEZE_TOKEN bearer token, if the server set one (optional)
```

If `AGENT_SQUEEZE_URL` is not set, tell the user to start the service first:

```
export OPENROUTER_API_KEY=sk-or-v1-...
agent-squeeze-serve --port 8765
```

## Squeeze one transcript

POST the conversation to `/v1/squeeze`. `task` is the agent's objective
(not a compression instruction). Omit it and the service infers the task
from the first user message.

```bash
curl -s -X POST "$AGENT_SQUEEZE_URL/v1/squeeze" \
  -H "Content-Type: application/json" \
  ${AGENT_SQUEEZE_TOKEN:+-H "Authorization: Bearer $AGENT_SQUEEZE_TOKEN"} \
  -d @- <<'EOF' > squeezed.json
{
  "messages": [
    {"role": "user", "content": "Fix the NullPointerException in PaymentProcessor"},
    {"role": "assistant", "content": "I'll search the codebase for it."},
    {"role": "tool", "content": "...30k tokens of tool output..."}
  ],
  "task": "Fix the NullPointerException in PaymentProcessor"
}
EOF
```

The response is `{"messages": [...], "stats": {...}}` with
`reduction_pct`, `kept`/`dropped` counts, latency and cost. Replace your
working transcript with the returned messages.

## Squeeze a fleet (multiple agents at once)

POST `/v1/squeeze-fleet` with one entry per agent. The service first
dedups identical tool outputs *across* agents (the first agent keeps the
content, the rest get a reference marker), then squeezes each agent
individually:

```bash
curl -s -X POST "$AGENT_SQUEEZE_URL/v1/squeeze-fleet" \
  -H "Content-Type: application/json" \
  ${AGENT_SQUEEZE_TOKEN:+-H "Authorization: Bearer $AGENT_SQUEEZE_TOKEN"} \
  -d '{"transcripts": {"frontend": [...], "backend": [...]}}'
```

Response: `{"transcripts": {...}, "report": {...}}`.

## Rules

- Never summarize during compression. The service decides keep/drop;
  your job is to hand it the transcript and use what comes back.
- Verify critical facts survived: after squeezing, check that the
  identifiers your task depends on (error names, versions, ports, file
  paths) are still present before continuing.
- The keep/drop threshold defaults to 0.5 and is calibrated — do not
  lower it to chase a bigger number. Dropped evidence is not recoverable.
