"""Data-pipeline agent compression: verbose DAG/step runs -> signal.

Data-pipeline agents (ETL/ELT harnesses, Airflow-style step runners,
dbt/spark wrappers) bloat context in predictable ways: heartbeat/progress
bars repeated every poll interval, full schema dumps (DDL) echoed per step,
row samples re-printed after every transform, connection banners, and
retry/heartbeat noise. What matters downstream is much smaller: per-step
status (ok/failed + error), row counts and timings, schema *changes* (not
repeats of the same schema), and data samples only when they show an
anomaly.

This module squeezes a run step-by-step with cross-step memory
(`seen_schemas`, `seen_log_patterns`) so repeats collapse to one-liners.
Decisions per step: "keep_full" (errors/tracebacks, final metrics, first
schema, anomalous samples), "keep_excerpt" (verbatim signal lines — counts,
durations, schema diffs — rest held off-context), "notice" (heartbeats,
progress bars, exact-duplicate schema dumps, repeated connection banners).
Nothing kept is ever rewritten — the admit-gate rule. Held text returns
byte-identically via `HoldStore` refs.
"""
import re

from .admit import HoldStore
from .messages import estimate_tokens

# Per-step noise that never carries new information.
PROGRESS_RE = re.compile(
    r"(^\s*(\d+\s*/\s*\d+|\d+%)\s*[\[|#=-]*\s*$|"
    r"progress\s*:\s*\d+\s*%|"
    r"heartbeat\b|"
    r"poll\s+#\d+\s*:\s*no new (data|records)|"
    r"waiting for (data|upstream|executor)|"
    r"still (running|processing|loading)|"
    r"checkpoint\s+saved|"
    r"\.\.\.\s*done\s*$|"
    r"rows\s+processed\s*:\s*\d+$)", re.IGNORECASE)

# Connection/session banners printed by every step.
BANNER_RE = re.compile(
    r"(connecting to|connected to|established connection|"
    r"session started|authenticated as|using profile|"
    r"version\s+\d+(\.\d+)+|"
    r"spark session|warehouse\s*:\s*\w+)", re.IGNORECASE)

# Errors: always keep verbatim — a dropped traceback is a dropped diagnosis.
ERROR_RE = re.compile(
    r"(traceback|exception|error|failed|fatal|raise |assert|"
    r"column .* not found|constraint (violation|failed)|"
    r"out of memory|oom|timeout|deadlock|permission denied)",
    re.IGNORECASE)

# Schema-ish lines: DDL, JSON-schema keys, pandas dtypes dumps.
SCHEMA_RE = re.compile(
    r"(create\s+table|alter\s+table|\bvarchar\b|\bbigint\b|\btimestamp\b|"
    r"primary\s+key|foreign\s+key|\bnot\s+null\b|"
    r"^\s*\w+\s+(string|int(eger)?|float|double|bool(ean)?|date)\b|"
    r'"type"\s*:\s*"(string|integer|number|boolean)"|'
    r"dtype\s*:|"
    r"schema\s*:)", re.IGNORECASE)

# Final metrics lines: counts, durations, checksums — always signal.
METRIC_RE = re.compile(
    r"(rows?\s*(read|written|loaded|processed|inserted|updated|deleted|"
    r"scanned|skipped|rejected)\s*[:=]?\s*[\d,]+|"
    r"total\s+rows\s*[:=]\s*[\d,]+|"
    r"duration\s*[:=]?\s*\d+(\.\d+)?\s*(s|sec|ms|min)|"
    r"elapsed\s*[:=]?\s*\d+(\.\d+)?\s*(s|sec|ms|min)|"
    r"(checksum|md5|sha\d*)\s*[:=]?\s*[0-9a-f]{6,}|"
    r"step\s+(completed|succeeded|failed)\b|"
    r"pipeline\s+(completed|succeeded|failed)\b)", re.IGNORECASE)

FULL_KEEP_CHARS = 400  # very short steps: judging costs more than it saves


def _normalize(line):
    return re.sub(r"\s+", " ", line.strip().lower())


def _signal_lines(text):
    """Verbatim keep-lines: errors, metrics, schema, anomalous data rows."""
    out = []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        if (ERROR_RE.search(s) or METRIC_RE.search(s) or SCHEMA_RE.search(s)):
            out.append(s)
    return out


def deterministic_policy(step, seen_schemas, seen_patterns):
    """Free offline judge. Returns (decision, excerpt).

    `seen_schemas` / `seen_patterns` are sets mutated across steps — the
    cross-step memory so repeated schemas and log patterns collapse.
    """
    text = step.get("text", "") or ""
    name = step.get("name", "step")
    if len(text) <= FULL_KEEP_CHARS:
        return "keep_full", text
    # Error in the step: never compress a failure.
    if ERROR_RE.search(text):
        return "keep_full", text
    # Pure progress/heartbeat noise -> one-liner.
    lines = [l for l in text.split("\n") if l.strip()]
    if lines and all(PROGRESS_RE.search(l) or BANNER_RE.search(l) for l in lines):
        return "notice", f"[{name}: {len(lines)} progress/heartbeat lines]"
    # Schema already seen verbatim -> collapse to a notice.
    schema_lines = [l.strip() for l in lines if SCHEMA_RE.search(l)]
    if schema_lines:
        key = _normalize("\n".join(sorted(schema_lines)))
        if key in seen_schemas:
            return "notice", (f"[{name}: schema identical to earlier step]")
        seen_schemas.add(key)
    # Mostly repetitive log pattern (same normalized line > 5x) -> collapse.
    counts = {}
    for l in lines:
        k = _normalize(re.sub(r"\d[\d,\.]*", "#", l))
        counts[k] = counts.get(k, 0) + 1
    if counts and max(counts.values()) >= 6 and len(counts) <= 3:
        sig = _signal_lines(text)
        excerpt = "\n".join(sig) if sig else f"[{name}: pattern x{max(counts.values())}]"
        return "keep_excerpt", excerpt
    sig = _signal_lines(text)
    if not sig:
        return "notice", f"[{name}: no metrics/schema/errors]"
    return "keep_excerpt", "\n".join(sig)


def squeeze_pipeline_run(steps, task=None, policy_fn=None, store=None):
    """Compress a data-pipeline run. Returns (squeezed_steps, stats)."""
    store = store or HoldStore()
    policy = policy_fn or deterministic_policy
    seen_schemas, seen_patterns = set(), set()
    out, stats = [], {"chars_in": 0, "chars_out": 0,
                      "keep_full": 0, "keep_excerpt": 0, "notice": 0}
    for step in steps:
        text = step.get("text", "") or ""
        stats["chars_in"] += len(text)
        decision, excerpt = policy(step, seen_schemas, seen_patterns)
        stats[decision] += 1
        if decision == "notice":
            ref = store.hold(step.get("name","step"), text)
            body = f"{excerpt}\n[held:{ref}]"
        elif decision == "keep_excerpt":
            ref = store.hold(step.get("name","step"), text)
            body = f"{excerpt}\n[held:{ref}]"
        else:
            body = text
        stats["chars_out"] += len(body)
        out.append({"name": step.get("name", "step"), "decision": decision,
                    "text": body})
    stats["chars_reduced_pct"] = round(
        100 * (1 - stats["chars_out"] / max(1, stats["chars_in"])), 1)
    stats["tokens_in"] = max(1, stats["chars_in"] // 4)
    stats["tokens_out"] = max(1, stats["chars_out"] // 4)
    return out, stats
