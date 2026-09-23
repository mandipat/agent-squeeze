"""Synthetic data-pipeline run -> squeeze -> needle recall check."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from agent_squeeze.pipeline import squeeze_pipeline_run, deterministic_policy  # noqa: E402
from agent_squeeze.admit import HoldStore  # noqa: E402

DDL = """CREATE TABLE users_events (
  user_id BIGINT NOT NULL,
  event_type VARCHAR(64) NOT NULL,
  event_ts TIMESTAMP NOT NULL,
  payload JSON,
  PRIMARY KEY (user_id, event_ts)
);"""

HEARTBEAT = "\n".join(
    f"poll #{i}: no new data | heartbeat" for i in range(1, 61))

STEPS = [
    {"name": "connect",
     "text": "\n".join([
         "connecting to warehouse: prod-us-east-1",
         "authenticated as svc_etl",
         "Spark session 3.5.1 started",
         "warehouse: PROD",
         "session started",
     ])},
    {"name": "extract",
     "text": "\n".join([
         "extract step started",
         DDL,
         HEARTBEAT,
         "rows read: 1,000,000",
         "duration: 142.5s",
         "checksum md5: 9f2c1ad4b8",
     ])},
    {"name": "transform",
     "text": "\n".join([
         "transform step started",
         DDL,  # same schema echoed again -> notice
         "applying 12 mapping rules",
         "rows processed: 1,000,000",
         "duration: 88.2s",
         "\n".join(f"rule_{i:02d}: ok (no skew)" for i in range(12)),
     ])},
    {"name": "validate",
     "text": "\n".join([
         "validation step started",
         "Traceback (most recent call last):",
         "  File \"validate.py\", line 41, in check",
         "    raise ValueError('negative revenue for order OR-99120')",
         "ValueError: negative revenue for order OR-99120",
         "constraint violation: CHECK (revenue >= 0)",
         "rows rejected: 3",
     ])},
    {"name": "load",
     "text": "\n".join([
         "load step started",
         "rows written: 999,997",
         "duration: 61.0s",
         "checksum md5: 7b31e0aa5c",
         "pipeline completed: FAILED (validate errors)",
     ])},
]

NEEDLES = ["OR-99120", "9f2c1ad4b8", "999,997", "rows rejected: 3"]


def main():
    steps, stats = squeeze_pipeline_run(
        STEPS, task="etl pipeline",
        policy_fn=lambda s, a, b: deterministic_policy(s, a, b),
        store=HoldStore())
    print(json.dumps(stats, indent=2))
    squeezed_text = "\n".join(s["text"] for s in steps)
    found = [n for n in NEEDLES if n in squeezed_text]
    print(f"needles: {len(found)}/{len(NEEDLES)} -> {found}")
    for s in steps:
        print(f"- {s['name']}: {s['decision']}")
    assert len(found) == len(NEEDLES), "needle lost"
    # validate step must be verbatim (error) and load must keep metrics
    by_name = {s["name"]: s for s in steps}
    assert by_name["validate"]["decision"] == "keep_full"
    assert by_name["extract"]["decision"] == "keep_excerpt"
    assert by_name["transform"]["decision"] == "notice", by_name["transform"]
    assert by_name["load"]["decision"] in ("keep_full", "keep_excerpt")
    print("OK")


if __name__ == "__main__":
    main()
