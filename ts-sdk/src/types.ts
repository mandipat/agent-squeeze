/** Shared types for the agent-squeeze TypeScript SDK. */

/** A single chat message in OpenAI/Anthropic shape. */
export interface Message {
  role: "system" | "user" | "assistant" | "tool" | string;
  content: string;
  [k: string]: unknown;
}

/** Options accepted by {@link AgentSqueezeClient}. */
export interface ClientOptions {
  /** Base URL of the agent-squeeze service, e.g. `http://localhost:8471`. */
  baseUrl?: string;
  /**
   * Bearer token. Sent as `Authorization: Bearer <token>`.
   * Falls back to the `AGENT_SQUEEZE_TOKEN` env var.
   */
  token?: string;
  /** Per-request timeout in ms. Defaults to 30_000. */
  timeoutMs?: number;
}

/** Statistics returned alongside a squeezed transcript. */
export interface SqueezeStats {
  tokens_before: number;
  tokens_after: number;
  reduction_pct?: number;
  protected_tokens?: number;
  [k: string]: unknown;
}

export interface SqueezeResult {
  messages: Message[];
  stats: SqueezeStats;
}

export interface FleetResult {
  transcripts: Record<string, Message[]>;
  report: Record<string, unknown>;
}

/** Admit-time gate decision for a single tool result. */
export type AdmitDecision = "keep_full" | "trim" | "notice" | "hold";

export interface Admission {
  name: string;
  decision: AdmitDecision;
  admitted_text: string;
  ref: string | null;
  held_chars: number;
  reduction_pct?: number;
}

export interface AdmitBatchResult {
  admissions: Admission[];
  stats: Record<string, unknown>;
}

export interface ReadmitResult {
  ref: string;
  text: string;
}

export interface ReadmitIfMentionedResult {
  found: Record<string, string>;
}

/** Prompt-cache TTL recommendation (5-min vs 1-hour Anthropic TTL). */
export interface TtlRecommendation {
  recommended: "5min" | "1hour";
  cost_5min_usd: number;
  cost_1hour_usd: number;
  saving_pct: number;
  hit_rate_5min: number;
  hit_rate_1hour: number;
}

/** Error raised for non-2xx responses. */
export class AgentSqueezeError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly body: unknown,
  ) {
    super(message);
    this.name = "AgentSqueezeError";
  }
}
