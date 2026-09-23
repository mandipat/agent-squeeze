# agent_squeeze — 60-second quickstart (no API key)

Two agents run at the same time — a frontend agent chasing a webhook
`TRANSACTION_TIMEOUT_77X` failure and a backend agent chasing a slow query
`SLOWQ_9ab2`. Both re-ran the same install (identical boilerplate) and both
sat through polling noise. `run.py` squeezes the fleet with the **free
deterministic policy** (no Jev call, no key, $0) and verifies both needles
survive.

```bash
git clone https://github.com/mandipat/agent-squeeze && cd agent-squeeze
python quickstart/run.py
```

Expected output:

```
=== agent_squeeze quickstart (free policy, no API key) ===
  frontend     3584 ->   1634 tokens ( 54.4%), $0.000000 in 0.0s
  backend      5134 ->   1591 tokens ( 69.0%), $0.000000 in 0.0s
  fleet       12144 ->   3225 tokens (73.4% reduction), 1 cross-agent duplicates, $0.000000 total

needle check (must all survive):
  frontend  OK
  backend   OK
```

What happened:

1. **Cross-agent exact dedup (pass 1)** — both agents' install output was
   byte-identical, so the backend's copy became a `[shared context]` ref to
   the frontend's. 1 duplicate removed before any judging.
2. **Boilerplate drop (pass 2)** — polling heartbeats and healthcheck pings
   dominate their chunks → dropped as noise.
3. **Needle survival** — the webhook failure line and the 41.7s slow-query
   line live in unique chunks → kept. The fail-safe (never empty a tool
   result entirely) guarantees a needle is never dropped even if the judge
   misfires.

## With a real key

The free policy mirrors the Jev FRAMING guidance (drop repeated
boilerplate, routine listings, heartbeats). Swap in the real judge:

```bash
export OPENROUTER_API_KEY=<your key>
agent-squeeze-serve --port 8765 &
curl -s -X POST localhost:8765/v1/squeeze-fleet \
  -H "Content-Type: application/json" \
  -d @quickstart/fleet.json
```

`POST /v1/squeeze-fleet` accepts the same `{"transcripts": {...}}` shape as
`quickstart/fleet.json`. Full endpoint list and the Claude Code plugin are
in the main [README](../README.md).
