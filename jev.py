"""TypeSafe Jev client via OpenRouter's decisions endpoint.

Jev is a calibrated System-1 decision model: its noul probabilities are
usable as absolute cutoffs (p >= 0.5), unlike uncalibrated local models.
Proven on 30k-token transcripts: 70-1300ms per call, ~$0.001/call.
"""
import json
import os
import urllib.request

API_URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"

FRAMING = (
    "You are a precise context-pruning judge for an autonomous AI coding agent. "
    "Decide which transcript chunks are REQUIRED for the agent to complete its task. "
    "Keep error rows, anomalies, test failures, stack traces, and anything the "
    "final answer depends on. Drop boilerplate: heartbeats, health checks, "
    "routine listings, repeated identical outputs."
)


def _key():
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set in the environment")
    return key


def decide(state, questions, timeout=180):
    body = json.dumps({"model": MODEL, "state": state,
                       "questions": questions}).encode()
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Authorization": "Bearer " + _key(),
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def score_chunks(chunks, task, framing=FRAMING):
    """chunks: list of str. Returns (probs, cost_usd) with probs[i] in [0,1]."""
    state = framing + "\n\nAgent task: " + task
    questions = {
        f"chunk_{i}": {
            "type": "noul",
            "instructions": (
                "Is the following transcript chunk needed for the agent to "
                f"complete its task? Answer true or false.\n\nChunk:\n{text}")
        } for i, text in enumerate(chunks)
    }
    resp = decide(state, questions)
    probs = [resp["answers"][f"chunk_{i}"]["noul"] for i in range(len(chunks))]
    cost = (resp.get("usage") or {}).get("cost", 0.0)
    return probs, cost
