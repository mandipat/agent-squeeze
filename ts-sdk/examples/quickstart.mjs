import { AgentSqueezeClient } from "@agent-squeeze/sdk";

// Points at the local agent-squeeze service by default.
// Start it first:  agent-squeeze-serve --port 8471
// (Optional) export AGENT_SQUEEZE_TOKEN=<redacted> if the service requires one.
const client = new AgentSqueezeClient({ baseUrl: "http://localhost:8471" });

// 1. Cache-aware squeeze: the prefix stays byte-identical (prompt-cache safe).
const transcript = [
  { role: "system", content: "You are a careful SRE copilot." },
  { role: "user", content: "Why did the deploy fail?" },
  { role: "tool", content: "poll ok\n".repeat(800) }, // repetitive boilerplate
];
const { messages, stats } = await client.squeezeCacheAware(
  transcript,
  "debug the deploy failure",
  1024,
);
console.log("reduction:", stats.reduction_pct, "tokens kept:", stats.tokens_after);

// 2. Admit-time gate: judge tool results at write time, hold the rest.
const admission = await client.admit("deploy-logs", "poll ok\n".repeat(800));
console.log("decision:", admission.decision, "ref:", admission.ref);

// 3. Recover held verbatim text later, byte-identical.
if (admission.ref) {
  const { text } = await client.readmit(admission.ref);
  console.log("readmitted chars:", text.length);
}
