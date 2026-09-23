# @agent-squeeze/sdk

TypeScript SDK for [agent-squeeze](https://github.com/mandipat/agent-squeeze) —
token compression, cache-aware squeeze, and admit-time tool-result gating.

Zero runtime dependencies. Node ≥ 18 (uses global `fetch`).

## Install

```bash
npm i @agent-squeeze/sdk
```

## Quickstart

```ts
import { AgentSqueezeClient } from "@agent-squeeze/sdk";

// Start the service first:  agent-squeeze-serve --port 8471
const client = new AgentSqueezeClient({ baseUrl: "http://localhost:8471" });
// or:  new AgentSqueezeClient()  with AGENT_SQUEEZE_TOKEN set if the
// service requires a bearer token (AGENT_SQUEEZE_TOKEN env).

// Cache-aware squeeze: prefix stays byte-identical (prompt-cache safe).
const { messages, stats } = await client.squeezeCacheAware(
  transcript,
  "debug the deploy failure",
  1024, // protect_tokens
);
console.log("reduction:", stats.reduction_pct);

// Admit-time gate: judge tool results at write time.
const adm = await client.admit("deploy-logs", bigLogText);
console.log(adm.decision); // keep_full | trim | notice | hold

// Re-admit held text later (byte-identical roundtrip).
if (adm.ref) {
  const { text } = await client.readmit(adm.ref);
}
```

See `examples/quickstart.mjs` for a runnable end-to-end script.

## API

| method | endpoint | notes |
|---|---|---|
| `squeeze(msgs, task?, threshold?)` | `POST /v1/squeeze` | Jev keep/drop squeeze |
| `squeezeCacheAware(msgs, task?, protectTokens?, threshold?)` | `POST /v1/squeeze-cache-aware` | byte-identical protected prefix |
| `squeezeFleet(transcripts, task?, threshold?)` | `POST /v1/squeeze-fleet` | many transcripts at once |
| `admit(name, text, task?)` | `POST /v1/admit` | write-time tool-result gate |
| `admitBatch(results, task?)` | `POST /v1/admit-batch` | batch admission |
| `readmit(ref)` | `POST /v1/readmit` | byte-identical hold recovery |
| `readmitIfMentioned(text)` | `POST /v1/readmit-if-mentioned` | re-admit refs named in a follow-up |

All methods throw `AgentSqueezeError` (with `.status` and `.body`) on
non-2xx responses — e.g. `readmit("unknown")` → 404.

## Develop

```bash
npm run build   # tsc -> dist/
npm test        # build + node:test against a stub HTTP service (offline)
```

## Publish

```bash
npm run build && npm publish --access public
```
