"""Support-chatbot conversation compression.

Support histories bloat in predictable ways: greetings, the same identity
verification re-asked by every handoff, template apologies ("I apologize
for the inconvenience"), hold-music equivalents, and "is there anything
else I can help you with" loops. What matters for resolution and handoff is
much smaller: the identity facts established *once*, the issue, what was
tried (and what happened), and the final resolution summary.

This module squeezes a thread turn-by-turn with a cross-turn memory so
repeats collapse to a one-liner. Decisions per turn: "keep_full" (first
establishment of identity facts, resolution summary, policy/refund facts),
"keep_excerpt" (verbatim fact lines, rest held off-context), "notice"
(greetings, closings, template apologies, exact-duplicate script steps,
re-asked verification). Nothing kept is ever rewritten — the admit-gate
rule. Held text returns byte-identically via `HoldStore` refs.
"""
import re

from .admit import HoldStore
from .messages import estimate_tokens

# No-information pleasantries: greetings, closings, template empathy.
PLEASANTRY_RE = re.compile(
    r"^(hello|hi|hey|good (morning|afternoon|evening)|welcome|"
    r"thanks?( you)?( for (reaching out|contacting|your patience|choosing))?|"
    r"have a (great|nice|wonderful) (day|evening)|"
    r"is there anything else( i can (help|do for))? (you (with )?)?(today|for you)?|"
    r"let me know if( there's| there is) anything else|"
    r"(please )?hold (on |)for a (moment|minute)|"
    r"one moment (please )?while i|"
    r"how can i help (you)?|what can i (do|help).{0,20}for you|"
    r"thank you for (your patience|holding|waiting))\W?.{0,60}$",
    re.IGNORECASE)

# Template apologies: pure token cost, carry no facts.
APOLOGY_RE = re.compile(
    r"(i('m| am) (so |very |really )?sorry|i apologize|apologies) for "
    r"(the inconvenience|any inconvenience|the trouble|the delay|the wait|"
    r"your experience|this experience)|"
    r"i (completely |totally |fully )?understand (how|that).{0,40}"
    r"(frustrating|upsetting|inconvenient|difficult)|"
    r"i('m| am) sorry to hear (that|about)|"
    r"thank you for your understanding.{0,40}$",
    re.IGNORECASE)

# Lines that establish a fact: case/refund/identity values, resolution.
FACT_RE = re.compile(
    r"(case|ticket|reference|order|account|confirmation|invoice)\s*(#|no\.?|number)?"
    r"\s*[:#]?\s*[\w\-]{4,}|"
    r"(refund|credit|charge(d)?|balance|total)\s+of?\s*\$?[\d,]+(\.\d{2})?|"
    r"\b(dob|date of birth|ssn|last four)\b.{0,20}\d|"
    r"(resolved|resolution|fixed|escalated|processed|shipped|"
    r"scheduled|cancelled|refund issued)",
    re.IGNORECASE)

# Re-asked verification: the same fact requested again later in the thread.
REVERIFY_RE = re.compile(
    r"\b(confirm|verify|provide|re-?enter|share|again|once more)\b.{0,40}"
    r"(account|date of birth|dob|phone|email|address|ssn|last four|"
    r"order number|case number)",
    re.IGNORECASE)

FULL_KEEP_CHARS = 200  # very short turns: judging costs more than it saves


def _normalize(line):
    return re.sub(r"\s+", " ", line.strip().lower())


def _fact_lines(text):
    return [l.strip() for l in text.split("\n")
            if l.strip() and FACT_RE.search(l)]


def deterministic_policy(turn, seen_templates, seen_facts):
    """Free offline judge. Returns (decision, excerpt).

    `seen_templates` / `seen_facts` are sets mutated across turns so repeats
    collapse to notices — the cross-turn memory of this module.
    """
    text = (turn.get("text") or "")
    lines = [l for l in (ln.strip() for ln in text.split("\n")) if l]
    if not lines:
        return "notice", "[empty turn]"
    if all(PLEASANTRY_RE.match(l) for l in lines):
        return "notice", "[pleasantries]"
    if all(APOLOGY_RE.search(l) for l in lines):
        return "notice", "[template apology]"

    # Exact-duplicate script step seen earlier in this thread.
    norm = _normalize(text)
    if norm in seen_templates and len(norm) > 40:
        return "notice", "[duplicate of earlier turn]"
    if len(norm) > 40:
        seen_templates.add(norm)

    # Re-asked verification whose facts were already established.
    if any(REVERIFY_RE.search(l) for l in lines):
        if seen_facts:
            return "notice", "[re-verification; facts established earlier]"
        return "keep_full", text  # first verification: facts land here

    if len(text) <= FULL_KEEP_CHARS:
        for l in lines:
            if FACT_RE.search(l):
                seen_facts.add(_normalize(l))
        return "keep_full", text

    facts = _fact_lines(text)
    for f in facts:
        seen_facts.add(_normalize(f))
    if facts:
        return "keep_excerpt", "\n".join(facts)
    if len(text) > 600:
        return "notice", "[no facts]"
    return "keep_full", text


def squeeze_support_thread(turns, task="", store=None, policy_fn=None):
    """Compress a support thread. Returns (kept_turns, stats).

    kept_turns: list of {"role", "decision", "text" or "excerpt"/"ref"}.
    stats: chars/tokens in/out, reduction %, per-decision counts.
    """
    store = store or HoldStore()
    seen_templates, seen_facts = set(), set()
    out, stats = [], {"turns": len(turns), "keep_full": 0, "keep_excerpt": 0,
                      "notice": 0, "chars_in": 0, "chars_out": 0,
                      "tokens_in": 0, "tokens_out": 0, "held": 0}
    for turn in turns:
        text = turn.get("text") or ""
        role = turn.get("role", "agent")
        stats["chars_in"] += len(text)
        decision, excerpt = (policy_fn(turn, seen_templates, seen_facts)
                             if policy_fn else deterministic_policy(
                                 turn, seen_templates, seen_facts))
        stats[decision] += 1
        entry = {"role": role, "decision": decision}
        if decision == "keep_full":
            entry["text"] = text
            stats["chars_out"] += len(text)
        else:
            ref = None
            if decision in ("keep_excerpt", "notice") and len(text) > 120:
                ref = store.hold(f"support:{decision}", text)
                stats["held"] += 1
                entry["ref"] = ref
            entry["excerpt"] = excerpt + (f" [held: {ref}]" if ref else "")
            stats["chars_out"] += len(entry["excerpt"])
        out.append(entry)
    stats["tokens_in"] = estimate_tokens(" ".join(t.get("text") or ""
                                                  for t in turns))
    stats["tokens_out"] = estimate_tokens(" ".join(
        e.get("text") or e.get("excerpt", "") for e in out))
    stats["reduction_pct"] = round(
        100 * (1 - stats["tokens_out"] / max(stats["tokens_in"], 1)), 2)
    return out, stats
