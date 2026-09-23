#!/usr/bin/env python3
"""Run Claude Code against the Aegis gateway with the stored credential.

Usage:
    aegis_claude.py -p "prompt" [claude args...]

Fetches a surrogate for custom.aegis from authd and injects it as
ANTHROPIC_AUTH_TOKEN; the egress layer swaps the surrogate for the real
token on the way out. The credential is never printed, logged, or written
to disk.
"""
import os
import sys

sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
from dynamic_credentials import dynamic_credential_entry  # noqa: E402


def main():
    entry = dynamic_credential_entry("custom.aegis")
    surrogate = str(entry["surrogate"]).strip()
    if not surrogate.startswith("hsurr:"):
        raise RuntimeError("authd did not return a surrogate value")
    env = dict(os.environ)
    env.update({
        "ANTHROPIC_AUTH_TOKEN": surrogate,
        "ANTHROPIC_BASE_URL": "https://gateway-aegis.internjobs.io/anthropic",
        "ANTHROPIC_CUSTOM_HEADERS": "x-model-provider: bedrock",
        "MAX_THINKING_TOKENS": "0",
        "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING": "1",
        "ANTHROPIC_MODEL": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    })
    os.execvpe("claude", ["claude"] + sys.argv[1:], env)


if __name__ == "__main__":
    main()
