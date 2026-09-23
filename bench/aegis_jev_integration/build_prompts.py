"""Build the two Aegis test prompts: full context vs Jev-squeezed context.

Same questions in both; transcripts rendered as ROLE/content blocks.
Writes prompt_full.txt and prompt_squeezed.txt + token estimates.
"""
import json

IN_FULL = "bench/inputs/skill_loads.json"
IN_JEV = "bench/aegis_jev_integration/skill_loads_jev.json"
OUT = "bench/aegis_jev_integration"

QUESTIONS = (
    "Questions:\n"
    "1. When a shopper signs in on a second device, what happens to their cart?\n"
    "2. What log message or event confirms the cart handover completed?\n\n"
    "Answer concisely: state what happens to the cart and quote the exact "
    "log message or event name that confirms completion."
)

PREAMBLE = (
    "You are answering questions about the following agent session transcript. "
    "Base your answer only on the transcript below.\n\n"
    "=== TRANSCRIPT START ===\n"
)


def render(messages):
    parts = []
    for m in messages:
        role = m.get("role", "?").upper()
        content = m.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content)
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


def main():
    with open(IN_FULL) as f:
        full = json.load(f)
    with open(IN_JEV) as f:
        jev = json.load(f)

    body_full = PREAMBLE + render(full["messages"]) + "\n=== TRANSCRIPT END ===\n\n" + QUESTIONS
    body_sq = (PREAMBLE + render(jev["messages_after"]) + "\n=== TRANSCRIPT END ===\n\n" + QUESTIONS)

    open(f"{OUT}/prompt_full.txt", "w").write(body_full)
    open(f"{OUT}/prompt_squeezed.txt", "w").write(body_sq)

    def est(chars):
        return chars // 4

    import os
    sz_f = os.path.getsize(f"{OUT}/prompt_full.txt")
    sz_s = os.path.getsize(f"{OUT}/prompt_squeezed.txt")
    print(f"full prompt:     {sz_f} chars (~{est(sz_f)} tok)")
    print(f"squeezed prompt: {sz_s} chars (~{est(sz_s)} tok)")
    print(f"reduction: {100*(sz_f-sz_s)/sz_f:.1f}%")
    # verify needles present in both
    for e in full["evidence"]:
        assert e in body_full, f"needle missing from full: {e!r}"
        assert e in body_sq, f"needle missing from squeezed: {e!r}"
    print("needles verified in both prompts:",
          [e[:50] for e in full["evidence"]])


if __name__ == "__main__":
    main()
