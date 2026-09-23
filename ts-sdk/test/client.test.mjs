import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { AgentSqueezeClient, AgentSqueezeError } from "../dist/index.js";

const SEEN = { auth: [] };

function startStub() {
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      SEEN.auth.push(req.headers["authorization"] ?? null);
      const json = (code, obj) => {
        res.writeHead(code, { "content-type": "application/json" });
        res.end(JSON.stringify(obj));
      };
      const data = body ? JSON.parse(body) : {};
      switch (req.url) {
        case "/v1/squeeze":
          return json(200, {
            messages: [{ role: "user", content: "hi" }],
            stats: { tokens_before: 10, tokens_after: 8 },
          });
        case "/v1/squeeze-cache-aware":
          return json(200, {
            messages: data.messages,
            stats: {
              tokens_before: 10,
              tokens_after: 9,
              protected_tokens: data.protect_tokens,
            },
          });
        case "/v1/squeeze-fleet":
          SEEN.fleetBody = data;
          return json(200, {
            transcripts: { a: data.transcripts.a },
            report: { n: 1 },
          });
        case "/v1/admit":
          return json(200, {
            name: data.name,
            decision: "notice",
            admitted_text: "[notice: boilerplate x800]",
            ref: "hold:abc123",
            held_chars: 11200,
            reduction_pct: 97.8,
          });
        case "/v1/admit-batch":
          return json(200, {
            admissions: data.results.map((r) => ({
              name: r.name,
              decision: "keep_full",
              admitted_text: r.text,
              ref: null,
              held_chars: 0,
            })),
            stats: { decisions: { keep_full: data.results.length } },
          });
        case "/v1/readmit":
          return data.ref === "hold:abc123"
            ? json(200, { ref: data.ref, text: "ORIGINAL".repeat(100) })
            : json(404, { error: "unknown ref" });
        case "/v1/readmit-if-mentioned":
          return json(200, {
            found: data.text.includes("hold:abc123")
              ? { "hold:abc123": "ORIGINAL" }
              : {},
          });
        case "/v1/recommend-ttl":
          return json(200, {
            recommended: data.turn_gaps_sec[0] > 300 ? "1hour" : "5min",
            cost_5min_usd: 1.48,
            cost_1hour_usd: 1.93,
            saving_pct: 23.3,
            hit_rate_5min: 1.0,
            hit_rate_1hour: 1.0,
          });
        case "/v1/prune-tools":
          return json(200, {
            tools: data.tools.slice(0, 1),
            ledger: data.tools.map((t) => ({
              name: t.name,
              decision: "keep",
              score: 0.75,
              reason: "stub",
            })),
            stats: {
              tools_before: data.tools.length,
              tools_after: 1,
              tokens_before: 100,
              tokens_after: 25,
              reduction_pct: 75.0,
              kept: 1,
              pruned: data.tools.length - 1,
            },
          });
        case "/v1/readmit-tool":
          return data.name === "gh"
            ? json(200, { name: "gh", text: "GH DEFINITION" })
            : json(404, { error: "unknown tool" });
        default:
          return json(404, { error: "not found" });
      }
    });
  });
  return new Promise((resolve) => {
    server.listen(0, () => resolve(server));
  });
}

test("SDK round-trips all endpoints", async (t) => {
  const server = await startStub();
  t.after(() => server.close());
  const port = server.address().port;
  const client = new AgentSqueezeClient({
    baseUrl: `http://localhost:${port}`,
    token: "sekrit",
  });

  const sq = await client.squeeze([{ role: "user", content: "hi" }], "t", 0.5);
  assert.equal(sq.stats.tokens_after, 8);

  const ca = await client.squeezeCacheAware([{ role: "user", content: "x" }], "t", 4096);
  assert.equal(ca.stats.protected_tokens, 4096);

  const fl = await client.squeezeFleet({ a: [{ role: "user", content: "y" }] });
  assert.equal(fl.report.n, 1);
  assert.equal(SEEN.fleetBody.allow_near_dup, false);

  const fl2 = await client.squeezeFleet(
    { a: [{ role: "user", content: "y" }] },
    undefined,
    0.5,
    { allowNearDup: true },
  );
  assert.equal(fl2.report.n, 1);
  assert.equal(SEEN.fleetBody.allow_near_dup, true);

  const adm = await client.admit("poll", "heartbeat\n".repeat(800), "monitor");
  assert.equal(adm.decision, "notice");
  assert.equal(adm.reduction_pct, 97.8);

  const batch = await client.admitBatch([{ name: "ls", text: "a\nb" }]);
  assert.equal(batch.admissions[0].decision, "keep_full");

  const re = await client.readmit("hold:abc123");
  assert.equal(re.text, "ORIGINAL".repeat(100));

  const rim = await client.readmitIfMentioned("see hold:abc123 for details");
  assert.deepEqual(Object.keys(rim.found), ["hold:abc123"]);
  const rimNone = await client.readmitIfMentioned("nothing here");
  assert.deepEqual(rimNone.found, {});

  const ttlSlow = await client.recommendTtl([600, 900], 200000, 2000, 3.0);
  assert.equal(ttlSlow.recommended, "1hour");
  const ttlFast = await client.recommendTtl([30, 45], 200000, 2000, 3.0);
  assert.equal(ttlFast.recommended, "5min");
  assert.equal(ttlSlow.saving_pct, 23.3);

  const pt = await client.pruneTools(
    [
      { name: "gh", description: "github cli" },
      { name: "edit_image", description: "generate images" },
    ],
    "open a PR",
    ["bash"],
  );
  assert.equal(pt.stats.reduction_pct, 75.0);
  assert.equal(pt.tools.length, 1);
  assert.equal(pt.ledger.length, 2);

  const rt = await client.readmitTool("gh");
  assert.equal(rt.text, "GH DEFINITION");

  await assert.rejects(client.readmitTool("nope"), (e) => {
    assert.ok(e instanceof AgentSqueezeError);
    assert.equal(e.status, 404);
    return true;
  });

  // auth header is sent on every call
  assert.ok(SEEN.auth.every((h) => h === "Bearer sekrit"));

  // errors carry status + body
  await assert.rejects(client.readmit("nope"), (e) => {
    assert.ok(e instanceof AgentSqueezeError);
    assert.equal(e.status, 404);
    return true;
  });
});
