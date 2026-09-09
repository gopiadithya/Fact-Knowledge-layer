"""Phase A: group raw predicate labels into families.

A family is a retrieval device. It says two claims are worth comparing; it never
says they are the same fact - `metric_canonical` is what says that. The prompt
sees labels only: no values, no numbers, no page text, so this call cannot put a
wrong figure into the knowledge layer.
"""
from typing import Dict, List, Tuple

from ..config import settings
from ..models.fact import Fact
from .llm_client import LLMUnavailable, generate_json

PROMPT_VERSION = "families-1"
SCHEMA_VERSION = "families-1"
MIN_MEMBER_CONFIDENCE = 0.6

SCHEMA = {
    "type": "object",
    "properties": {
        "families": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "family_id": {"type": "string"},
                    "family_label": {"type": "string"},
                    "members": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "raw": {"type": "string"},
                                "confidence": {"type": "number"},
                            },
                            "required": ["raw", "confidence"],
                        },
                    },
                },
                "required": ["family_id", "family_label", "members"],
            },
        }
    },
    "required": ["families"],
}

PROMPT = """You group measurement labels taken from business, scientific and government documents.

Group the labels below into families. A family holds labels that name the SAME KIND of
measurable quantity, so that two documents using different words for one quantity end up
together. This grouping is used to decide which claims are worth comparing - it is not a
statement that the claims are identical.

Rules:
- Never put a level and a change in the same family. "EBITDA" and "EBITDA change" are different.
- Never merge labels whose scope clearly differs, such as a total and a single segment.
- When unsure, leave a label alone in its own family. A wrong grouping is worse than no grouping.
- family_id must be lower_snake_case and describe the quantity, not the document.
- confidence is your certainty that the label belongs in that family, from 0 to 1.
- Every label given to you must appear in exactly one family.

Labels:
{labels}
"""


def _call(labels: List[str]) -> Dict:
    """The seam tests monkeypatch. Raises LLMUnavailable on any failure."""
    s = settings()
    prompt = PROMPT.format(labels="\n".join(f"- {l}" for l in labels))
    return generate_json(prompt, SCHEMA, s.model_fast, PROMPT_VERSION, SCHEMA_VERSION)


def _fallback(facts: List[Fact]) -> None:
    for f in facts:
        f.predicate_family = f.metric_canonical
        f.predicate_family_confidence = 1.0


def _parse_families(payload: Dict) -> Dict[str, Tuple[str, float]]:
    """Turn a (possibly malformed) LLM payload into {raw_label: (family_id, confidence)}.

    generate_json only guarantees the top level is a dict - nothing validates the
    nested shape against SCHEMA, and a stale or hand-edited cache entry can drift
    from it too. Every level below the top is therefore treated as untrusted: a
    wrong shape at any point drops just that piece rather than raising, so a
    single bad family or member can never take down the whole grouping.
    """
    assigned: Dict[str, Tuple[str, float]] = {}
    families = payload.get("families", [])
    if not isinstance(families, list):
        return assigned
    for family in families:
        if not isinstance(family, dict):
            continue
        fid = str(family.get("family_id") or "").strip()
        if not fid:
            continue
        members = family.get("members", [])
        if not isinstance(members, list):
            continue
        for member in members:
            if not isinstance(member, dict):
                continue
            raw = str(member.get("raw") or "").strip()
            try:
                conf = float(member.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            if raw and conf >= MIN_MEMBER_CONFIDENCE:
                assigned[raw] = (fid, conf)
    return assigned


def assign_families(facts: List[Fact]) -> None:
    """Fill predicate_family on every fact. Never raises."""
    if not facts:
        return
    if not settings().llm_enabled():
        _fallback(facts)
        return

    labels = sorted({f.metric for f in facts if f.metric})
    if not labels:
        _fallback(facts)
        return
    try:
        payload = _call(labels)
    except LLMUnavailable:
        _fallback(facts)
        return

    try:
        assigned = _parse_families(payload)
    except Exception:
        # Belt and suspenders: _parse_families already guards every shape we can
        # think of, but a response family is never worth crashing ingest over,
        # so any other surprise still degrades to today's exact-key behaviour.
        _fallback(facts)
        return

    for f in facts:
        family, conf = assigned.get(f.metric, (f.metric_canonical, 1.0))
        # A change is its own quantity whatever the model decided, so keep the two apart.
        if f.metric_canonical.endswith(".change") and not family.endswith(".change"):
            family = f"{family}.change"
        f.predicate_family = family
        f.predicate_family_confidence = round(conf, 2)
