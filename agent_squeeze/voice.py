"""Voice/conversational-agent dialogue compression.

Spoken-dialogue transcripts bloat differently from chat transcripts. The
signal is the same — commitments, facts, outcomes — but the noise is
acoustic: ASR fillers ("um", "uh"), backchannels ("mm-hmm", "yeah",
"right"), barge-in fragments (cut-off utterances), confirmation loops
("just to confirm..."), TTS readbacks of long content already in system
data, and silence/noise events. Turns are short, so the per-turn judging
floor is lower than chat modules.

Barge-in rule (folded in from voice-agent practice — see PROGRESS.md Run
18 sources): only what the agent *actually spoke* may enter context.
A turn carrying ``interrupted=True`` should carry ``committed_text`` with
the spoken-so-far fragment; the policy keeps the committed text and drops
the unspoken remainder. An interrupted turn with no committed text is a
notice — never fabricate a full utterance the user never heard.

Decisions per turn: "keep_full" (commitments, outcomes, first fact
establishment), "keep_excerpt" (verbatim fact/commitment lines from a
noisy turn, rest held off-context), "notice" (fillers, backchannels,
restated repeats, confirmation re-asks, readbacks, silence). Nothing kept
is ever rewritten — the admit-gate rule. Held text returns byte-identically
via ``HoldStore`` refs.
"""
import re

from .admit import HoldStore
from .messages import estimate_tokens

# Pure backchannels: acknowledge the speaker, carry no content.
BACKCHANNEL_WORDS = frozenset(
    "mm mmhmm mhm hmm yeah yep yup right okay ok sure gotit got i see uh uhuh "
    "huh go on listening alright great cool nice exactly of course absolutely "
    "thank you thanks".split())

# ASR filler fragments: hesitation, false starts, abandoned phrases.
FILLER_WORDS = frozenset(
    "um umm uh uhh erm ah like you know mean so well let me see hang on sec second"
    .split())


def _words(text):
    return re.findall(r"\w+", text.lower())


def _is_backchannel(line):
    ws = [w for w in _words(line) if w not in ("i", "m", "am", "it")]
    return bool(ws) and all(w in BACKCHANNEL_WORDS for w in ws)


def _is_filler(line):
    ws = _words(line)
    return bool(ws) and all(w in FILLER_WORDS for w in ws)

# Confirmation re-asks: the agent re-requests a fact already established.
CONFIRM_RE = re.compile(
    r"\b(just to confirm|let me (just )?confirm|so (you'?re|that'?s)|"
    r"did you say|can you (please )?repeat|say that again|"
    r"sorry,? (what|who|how)|come again)\b.{0,60}"
    r"(name|address|phone|email|number|date|time|order|account|amount)",
    re.IGNORECASE)

# Lines that establish a commitment or outcome — always keep verbatim.
COMMIT_RE = re.compile(
    r"(booked|booking (confirmed|reference)|confirmed for|scheduled for|"
    r"order (placed|confirmed|number)|delivery (scheduled|set)|"
    r"appointment (set|confirmed)|reference (number|#)|"
    r"reservation (confirmed|#)|charged \$?[\d,]+(\.\d{2})?|"
    r"total (is|of|came to) \$?[\d,]+(\.\d{2})?|"
    r"confirmation (number|code|#)|tracking (number|#)|"
    r"reminder set|alarm set|timer set)",
    re.IGNORECASE)

# Fact lines worth keeping verbatim from a noisy turn.
FACT_LINE_RE = re.compile(
    r"\d{3}[-.\s]?\d{3}[-.\s]?\d{4}|"          # phone-ish
    r"\b\d{1,2}:\d{2}\s*(am|pm)\b|"            # times
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|"
    r"\$\d[\d,]*(\.\d{2})?|"                   # money
    r"\b\d{1,5}\s+\w+(\s+\w+){0,3}\s+(st|ave|rd|blvd|dr|ln|way|court|ct)\b",
    re.IGNORECASE)

FULL_KEEP_CHARS = 160  # spoken turns are short; judging costs more than it saves
HOLD_CHARS = 100        # hold longer turns so repeats/readbacks are recoverable


def _normalize(text):
    return re.sub(r"\s+", " ", text.strip().lower())


# Stopwords for the restatement-coverage check: content words carry the signal.
_STOPWORDS = frozenset(
    "i me my you your we the a an to of for on in is are was be do does did "
    "have has had will would can could should this that it and or so but not "
    "no yes what when where how".split())


def _content_words(text):
    return [w for w in re.findall(r"\w+", text.lower()) if w not in _STOPWORDS]


def _is_restatement(new_norm, earlier_norm):
    """A long ramble restates an earlier turn if it covers ~all of the
    earlier turn's content words (recall-style — Jaccard dilutes on long
    rambles, so this complements it)."""
    en = _content_words(earlier_norm)
    if len(en) < 5:
        return False
    bn = set(re.findall(r"\w+", new_norm.lower()))
    return sum(1 for w in en if w in bn) / len(en) >= 0.75


def _jaccard(a, b):
    a, b = set(re.findall(r"\w+", a.lower())), set(re.findall(r"\w+", b.lower()))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def deterministic_policy(turn, seen_repeats):
    """Free offline judge. Returns (decision, excerpt).

    `seen_repeats` is a list of normalized substantive turn texts mutated
    across turns so restated repeats collapse to notices — cross-turn
    memory via Jaccard similarity (ASR rephrases, so exact dedup misses).
    """
    text = turn.get("text") or ""
    lines = [l for l in (ln.strip() for ln in text.split("\n")) if l]
    role = turn.get("role", "user")

    # Barge-in rule: only what was actually spoken enters context.
    if turn.get("interrupted"):
        spoken = (turn.get("committed_text") or "").strip()
        if not spoken:
            return "notice", "[interrupted; nothing spoken]"
        return "keep_excerpt", "[interrupted; spoke only] " + spoken

    if not lines:
        return "notice", "[silence/no audio]"

    if all(_is_backchannel(l) for l in lines):
        return "notice", "[backchannel]"
    if all(_is_filler(l) for l in lines):
        return "notice", "[filler]"

    # Restated repeat: same intent in different words, seen earlier.
    # Jaccard catches near-identical rephrases; coverage catches long rambles
    # that dilute Jaccard but repeat an earlier turn's content words.
    norm = _normalize(text)
    if len(norm) > 30 and any(
            _jaccard(norm, s) >= 0.45 or _is_restatement(norm, s)
            for s in seen_repeats):
        return "notice", "[restated; same intent as earlier turn]"

    # Confirmation re-ask after the fact is established.
    if role == "agent" and CONFIRM_RE.search(text) and seen_repeats:
        return "notice", "[confirmation re-ask; fact established earlier]"

    # Commitments/outcomes: keep verbatim, always.
    if COMMIT_RE.search(text):
        if len(norm) > 30:
            seen_repeats.append(norm)
        return "keep_full", text

    # Fact lines from a noisy turn: verbatim excerpt, rest held.
    fact_lines = [l for l in lines if FACT_LINE_RE.search(l)]
    if fact_lines and len(text) > FULL_KEEP_CHARS:
        if len(norm) > 30:
            seen_repeats.append(norm)
        return "keep_excerpt", "\n".join(fact_lines)

    if len(text) <= FULL_KEEP_CHARS:
        if len(norm) > 30:
            seen_repeats.append(norm)
        return "keep_full", text

    # Long turn with no facts and no commitment: nothing worth judging down.
    if len(norm) > 30:
        seen_repeats.append(norm)
    return "keep_full", text


def squeeze_voice_dialogue(turns, task="", store=None, policy_fn=None):
    """Compress a spoken-dialogue transcript. Returns (kept_turns, stats).

    kept_turns: list of {"role", "decision", "text" or "excerpt"/"ref"}.
    stats: chars/tokens in/out, reduction %, per-decision counts.
    """
    store = store or HoldStore()
    seen_repeats = []
    out, stats = [], {"turns": len(turns), "keep_full": 0, "keep_excerpt": 0,
                      "notice": 0, "chars_in": 0, "chars_out": 0,
                      "tokens_in": 0, "tokens_out": 0, "held": 0}
    for turn in turns:
        text = turn.get("text") or ""
        role = turn.get("role", "user")
        stats["chars_in"] += len(text)
        decision, excerpt = (policy_fn(turn, seen_repeats) if policy_fn
                             else deterministic_policy(turn, seen_repeats))
        stats[decision] += 1
        entry = {"role": role, "decision": decision}
        if decision == "keep_full":
            entry["text"] = text
            stats["chars_out"] += len(text)
        else:
            ref = None
            if len(text) > HOLD_CHARS:
                ref = store.hold(f"voice:{decision}", text)
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
