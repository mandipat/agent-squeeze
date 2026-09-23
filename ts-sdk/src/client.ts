import {
  AdmitBatchResult,
  Admission,
  AgentSqueezeError,
  ClientOptions,
  FleetResult,
  Message,
  ReadmitIfMentionedResult,
  ReadmitResult,
  SqueezeResult,
  TtlRecommendation,
} from "./types.js";

/** Typed client for the agent-squeeze HTTP service. Zero runtime deps. */
export class AgentSqueezeClient {
  private readonly baseUrl: string;
  private readonly token?: string;
  private readonly timeoutMs: number;

  constructor(opts: ClientOptions = {}) {
    this.baseUrl = (opts.baseUrl ?? "http://localhost:8471").replace(/\/+$/, "");
    this.token = opts.token ?? process.env.AGENT_SQUEEZE_TOKEN;
    this.timeoutMs = opts.timeoutMs ?? 30_000;
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (this.token) headers["authorization"] = `Bearer ${this.token}`;
    const res = await fetch(`${this.baseUrl}${path}`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(this.timeoutMs),
    });
    const payload = await res.json().catch(() => null);
    if (!res.ok) {
      const msg =
        payload && typeof payload === "object" && "error" in payload
          ? String((payload as { error: unknown }).error)
          : `request failed with status ${res.status}`;
      throw new AgentSqueezeError(msg, res.status, payload);
    }
    return payload as T;
  }

  /** Squeeze a transcript (Jev keep/drop, or the service's free policy). */
  squeeze(
    messages: Message[],
    task?: string,
    threshold = 0.5,
  ): Promise<SqueezeResult> {
    return this.post("/v1/squeeze", { messages, task, threshold });
  }

  /**
   * Squeeze with a byte-identical protected prefix (prompt-cache safe).
   * Returns the prefix untouched and only squeezes the tail.
   */
  squeezeCacheAware(
    messages: Message[],
    task?: string,
    protectTokens = 1024,
    threshold = 0.5,
  ): Promise<SqueezeResult> {
    return this.post("/v1/squeeze-cache-aware", {
      messages,
      task,
      protect_tokens: protectTokens,
      threshold,
    });
  }

  /** Squeeze many transcripts at once (agent fleet). */
  squeezeFleet(
    transcripts: Record<string, Message[]>,
    task?: string,
    threshold = 0.5,
  ): Promise<FleetResult> {
    return this.post("/v1/squeeze-fleet", { transcripts, task, threshold });
  }

  /** Admit-time gate: judge one tool result before it enters context. */
  admit(name: string, text: string, task?: string): Promise<Admission> {
    return this.post("/v1/admit", { name, text, task });
  }

  /** Admit-time gate over a batch of tool results. */
  admitBatch(
    results: Array<{ name: string; text: string }>,
    task?: string,
  ): Promise<AdmitBatchResult> {
    return this.post("/v1/admit-batch", { results, task });
  }

  /** Readmit a held (off-context) tool result by ref. Byte-identical. */
  readmit(ref: string): Promise<ReadmitResult> {
    return this.post("/v1/readmit", { ref });
  }

  /** Re-admit any held results referenced by ref in `text`. */
  readmitIfMentioned(text: string): Promise<ReadmitIfMentionedResult> {
    return this.post("/v1/readmit-if-mentioned", { text });
  }

  /**
   * Recommend a prompt-cache TTL from the session's inter-turn gap pattern.
   * @param turnGapsSec gap in seconds before each turn (one value per turn).
   * @param prefixTokens protected prefix tokens (the cache entry).
   * @param dynamicTokens dynamic tail tokens per turn (always full price).
   * @param basePerMtok base model price in $/MTok input (Sonnet-class: 3.0).
   */
  recommendTtl(
    turnGapsSec: number[],
    prefixTokens: number,
    dynamicTokens = 0,
    basePerMtok = 3.0,
  ): Promise<TtlRecommendation> {
    return this.post("/v1/recommend-ttl", {
      turn_gaps_sec: turnGapsSec,
      prefix_tokens: prefixTokens,
      dynamic_tokens: dynamicTokens,
      base_per_mtok: basePerMtok,
    });
  }
}
