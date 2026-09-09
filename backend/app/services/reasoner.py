"""
Deterministic pairwise reasoning.

Given two facts about the same entity and metric, walk five checks and emit
one relationship with a readable trace:

  1. entity      2. metric      3. period      4. context (scope / basis /
  estimate-vs-actual / partial period)      5. value (or status text)

Verdicts: CORROBORATED, CONTRADICTION, CONTEXTUALLY_EXPLAINED. Pairs whose
units cannot be compared (INR vs USD, COUNT vs TONNES) yield no relationship.
"""
import datetime
import difflib
import re
from typing import List, Optional, Tuple

from ..models.fact import Fact, PeriodType
from ..models.relationship import FactRelationship, ReasoningCheck, ReasoningTrace, RelationshipType

TOLERANCE_PCT = 1.0        # relative tolerance for money/count values (rounding Cr vs Mn)
PCT_POINT_TOLERANCE = 0.05  # absolute tolerance for percentage metrics (6.4 vs 6.5 is a real difference)
IDENTIFIER_MIN_LENGTH = 6      # shorter tokens ("Q4", "V2") are codes/abbreviations, not identifiers
IDENTIFIER_REVISION_SIMILARITY = 0.88  # character-ratio floor for "this is the same identifier, revised"
_SCALE = {"crore": 1e7, "crores": 1e7, "cr": 1e7, "lakh": 1e5, "lakhs": 1e5, "million": 1e6, "millions": 1e6, "mn": 1e6,
          "m": 1e6, "billion": 1e9, "billions": 1e9, "bn": 1e9, "b": 1e9, "trillion": 1e12, "tn": 1e12, "k": 1e3, "thousand": 1e3}


def rounding_half_unit(raw: str) -> float:
    """How much a printed figure could be off by rounding: '1.4 Mn' -> 0.05 Mn, '1,429K' -> 0.5K, '8,142 Cr' -> 0.5 Cr."""
    m = re.search(r"(\d[\d,]*)(?:\.(\d+))?\s*([A-Za-z]+)?", raw)
    if not m:
        return 0.0
    decimals = len(m.group(2) or "")
    scale = _SCALE.get((m.group(3) or "").lower(), 1.0)
    return 0.5 * (10 ** -decimals) * scale


def text_similarity(a: str, b: str) -> float:
    """Token-set Jaccard on lower-cased alphanumerics; 'Rs.' vs 'Rupees' style noise is ignored."""
    ta = set(re.findall(r"[a-z0-9]{2,}", a.lower())) - {"the", "and", "of", "at", "in", "on"}
    tb = set(re.findall(r"[a-z0-9]{2,}", b.lower())) - {"the", "and", "of", "at", "in", "on"}
    if not ta or not tb:
        return 1.0 if a.strip().lower() == b.strip().lower() else 0.0
    return len(ta & tb) / len(ta | tb)


def is_identifier_shaped(value: str) -> bool:
    """Structural check for 'this value is a single opaque code, not prose' - a single token
    (no internal whitespace), long enough to be an identifier rather than an abbreviation, and
    mixing letters with digits the way a registration number, ISIN, DUNS number or catalogue
    code does. This is purely about shape: it names no domain, no country, no field."""
    t = value.strip()
    if not t or any(c.isspace() for c in t) or len(t) < IDENTIFIER_MIN_LENGTH:
        return False
    has_alpha = any(c.isalpha() for c in t)
    has_digit = any(c.isdigit() for c in t)
    return has_alpha and has_digit


def identifier_similarity(a: str, b: str) -> float:
    """Character-level similarity ratio (difflib SequenceMatcher) - the right instrument for a
    value that is one unbroken token, where token-set Jaccard degenerates to 0 or 1."""
    return difflib.SequenceMatcher(None, a, b).ratio()


def char_diff_summary(a: str, b: str) -> Tuple[int, int]:
    """(characters that differ, length of the longer value) between two short strings, using the
    same alignment difflib used to score them - so the count matches the similarity ratio."""
    total = max(len(a), len(b))
    matcher = difflib.SequenceMatcher(None, a, b)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return total - matched, total


class DeterministicReasoningEngine:
    @classmethod
    def evaluate_fact_pair(cls, a: Fact, b: Fact) -> Optional[FactRelationship]:
        if a.id == b.id:
            return None
        checks: List[ReasoningCheck] = []

        # 1. entity ------------------------------------------------------------------
        entity_ok = a.entity_canonical == b.entity_canonical
        checks.append(ReasoningCheck(step_number=1, name="Entity", passed=entity_ok,
                                     explanation=(f"Both facts are about '{a.entity}'" if entity_ok
                                                  else f"'{a.entity}' vs '{b.entity}'")))
        if not entity_ok:
            return None

        # 2. metric ------------------------------------------------------------------
        exact = a.metric_canonical == b.metric_canonical
        family_ok = bool(a.predicate_family) and a.predicate_family == b.predicate_family
        match_basis = "exact" if exact else ("family" if family_ok else "surface")
        metric_ok = exact or family_ok
        checks.append(ReasoningCheck(
            step_number=2, name="Metric", passed=metric_ok,
            explanation=(f"'{a.metric}' and '{b.metric}' both map to {a.metric_canonical}" if exact
                         else (f"'{a.metric}' and '{b.metric}' are different labels grouped into the same "
                               f"family ({a.predicate_family}); comparable, but not proof of one claim"
                               if family_ok else f"{a.metric_canonical} vs {b.metric_canonical}")),
            details={"match_basis": match_basis}))
        if not metric_ok:
            return None

        # A family match brought these together, which is weaker evidence than an
        # identical canonical key. It may confirm an agreement, but the uncertainty
        # the grouping introduced must never be reported as a conflict.
        family_only = match_basis != "exact"

        # units must be comparable (INR vs USD is not, without FX data)
        if a.numeric_value is not None and b.numeric_value is not None and a.normalized_unit != b.normalized_unit:
            return None

        ta, tb = a.raw_value.lower().strip(), b.raw_value.lower().strip()
        identifier_pair = is_identifier_shaped(ta) and is_identifier_shaped(tb)

        # 3. period ------------------------------------------------------------------
        pa, pb = a.period.canonical, b.period.canonical
        if identifier_pair:
            period_ok = True
            checks.append(ReasoningCheck(step_number=3, name="Period", passed=True,
                                         explanation="Permanent corporate identifier (reporting period not applicable)",
                                         details={"type": "corporate_identity"}))
        else:
            period_known = pa != "unknown" and pb != "unknown"
            period_ok = period_known and pa == pb
            checks.append(ReasoningCheck(step_number=3, name="Period", passed=period_ok,
                                         explanation=(f"Same reporting period ({pa})" if period_ok
                                                      else f"'{a.period.raw or pa}' vs '{b.period.raw or pb}'"),
                                         details={"a": pa, "b": pb}))

        # 4. context -----------------------------------------------------------------
        notes: List[str] = []
        sa, sb = a.context.scope, b.context.scope
        # standalone vs consolidated is a real difference; an explicitly standalone figure against an unstated one is
        # treated as a scope difference too, because company-level prose defaults to consolidated numbers.
        # A segment figure or industry-wide statistic is also not comparable to company-level totals.
        scope_conflict = sa != sb and ("unspecified" not in (sa, sb) or "standalone" in (sa, sb) or "segment" in (sa, sb) or "industry" in (sa, sb))
        if scope_conflict:
            notes.append(f"scope {sa} vs {sb}")
        elif sa != sb:
            notes.append(f"scope stated as '{sa if sa != 'unspecified' else sb}' in one source only (assumed comparable)")
        basis_conflict = a.context.accounting_standard != b.context.accounting_standard
        if basis_conflict:
            notes.append(f"basis '{a.context.accounting_standard}' vs '{b.context.accounting_standard}'")

        # Sub-metric comparison: total vs other_revenue, segment vs total, etc.
        sub_a, sub_b = getattr(a, "sub_metric", None), getattr(b, "sub_metric", None)
        sub_metric_conflict = bool(
            sub_a and sub_b and sub_a != sub_b
            and not ("unspecified" in (sub_a, sub_b))
            and not (sub_a == "total" and sub_b == "operations")
            and not (sub_a == "operations" and sub_b == "total")
        )
        if sub_metric_conflict:
            notes.append(f"sub-metric '{sub_a}' vs '{sub_b}'")

        qa, qb = set(a.context.qualifiers), set(b.context.qualifiers)
        estimate_diff = ("estimate" in qa) != ("estimate" in qb)
        partial_diff = ("partial_period" in qa) != ("partial_period" in qb)
        # a figure that is a slice of a larger whole ("the Group's share of net profit") is a
        # different quantity from the whole, however well the metric name and period line up
        share_diff = ("share_of" in qa) != ("share_of" in qb)
        # one figure explicitly excludes something the other does not ("excluding Spoton, we had ...")
        exclusion_diff = ("exclusions_apply" in qa) != ("exclusions_apply" in qb)
        if estimate_diff:
            notes.append("one figure is an estimate/projection, the other is reported")
        if partial_diff:
            notes.append("one figure covers only part of the period")
        if share_diff:
            notes.append("one figure is a share of a larger whole, the other is the whole")
        if exclusion_diff:
            notes.append("one figure states an exclusion the other does not")
        context_ok = not (scope_conflict or sub_metric_conflict or basis_conflict or estimate_diff or partial_diff
                          or share_diff or exclusion_diff)
        checks.append(ReasoningCheck(step_number=4, name="Context", passed=context_ok,
                                     explanation="Same scope, basis and qualifiers" if context_ok else "; ".join(notes),
                                     details={"a": a.context.model_dump(), "b": b.context.model_dump()}))

        # 5. value -------------------------------------------------------------------
        now = datetime.datetime.utcnow().isoformat()
        if a.numeric_value is not None and b.numeric_value is not None:
            va, vb = a.normalized_numeric_value or 0.0, b.normalized_numeric_value or 0.0
            delta_abs = abs(va - vb)
            biggest = max(abs(va), abs(vb))
            delta_pct = (delta_abs / biggest * 100.0) if biggest > 0 else 0.0
            rounding = max(rounding_half_unit(a.raw_value), rounding_half_unit(b.raw_value))
            if a.normalized_unit == "PERCENT":
                same_value = delta_abs <= max(PCT_POINT_TOLERANCE, rounding)
                how = f"{va:g} vs {vb:g} percentage points (difference {delta_abs:.2f} pp)"
            else:
                same_value = delta_pct <= TOLERANCE_PCT or delta_abs <= rounding
                how = f"{a.raw_value} = {va:,.0f} vs {b.raw_value} = {vb:,.0f} {a.normalized_unit} (difference {delta_pct:.2f}%)"
                if delta_pct > TOLERANCE_PCT and same_value:
                    how += ", within the rounding of the coarser figure"
            checks.append(ReasoningCheck(step_number=5, name="Value", passed=same_value, explanation=how,
                                         details={"delta_pct": round(delta_pct, 3), "delta_abs": delta_abs}))

            if same_value and period_ok and context_ok:
                conf = 0.80 if family_only else 0.95
                rel, why = RelationshipType.CORROBORATED, None
                summary = (f"Both sources report {a.metric} for {pa} as the same value "
                           f"({a.raw_value} in {a.document_name}; {b.raw_value} in {b.document_name}).")
            elif same_value and period_ok:
                conf = 0.72 if family_only else 0.85
                rel, why = RelationshipType.CORROBORATED, None
                summary = (f"Same value for {a.metric} in {pa} ({a.raw_value} vs {b.raw_value}); context differs only in "
                           f"how it was stated ({'; '.join(notes)}).")
            elif not period_ok:
                rel, why, conf = RelationshipType.CONTEXTUALLY_EXPLAINED, "DIFFERENT_PERIODS", 0.9
                summary = (f"Not a contradiction: {a.raw_value} and {b.raw_value} are {a.metric} for different periods "
                           f"({a.period.raw or pa} vs {b.period.raw or pb}).")
            elif basis_conflict or scope_conflict or sub_metric_conflict:
                rel, why, conf = RelationshipType.CONTEXTUALLY_EXPLAINED, (
                    "SUB_METRIC_DIFFERENCE" if sub_metric_conflict and not (basis_conflict or scope_conflict)
                    else "SCOPE_OR_BASIS"
                ), 0.88
                summary = (f"Not a contradiction: {a.raw_value} vs {b.raw_value} for {a.metric} in {pa} differ because of "
                           f"{'; '.join(n for n in notes if n.startswith(('scope', 'basis', 'sub-metric')))}.")
            elif estimate_diff:
                rel, why, conf = RelationshipType.CONTEXTUALLY_EXPLAINED, "DATA_VINTAGE", 0.85
                est, act = (a, b) if "estimate" in qa else (b, a)
                summary = (f"Apparent conflict explained by data vintage: {est.document_name} gives an estimate/projection "
                           f"({est.raw_value}) while {act.document_name} reports {act.raw_value} for {pa}.")
            elif share_diff:
                rel, why, conf = RelationshipType.CONTEXTUALLY_EXPLAINED, "DIFFERENT_DEFINITION", 0.85
                part, whole = (a, b) if "share_of" in qa else (b, a)
                summary = (f"Not a contradiction: these are different quantities. {part.raw_value} is a share of a "
                           f"larger whole ({part.source_evidence.exact_quote.strip()[:110]}...), while "
                           f"{whole.raw_value} is {whole.metric} itself. Same period ({pa}), different definitions.")
            elif exclusion_diff:
                rel, why, conf = RelationshipType.CONTEXTUALLY_EXPLAINED, "EXCLUSIONS_DIFFER", 0.8
                summary = (f"Apparent conflict explained by coverage: one of {a.raw_value} / {b.raw_value} for "
                           f"{a.metric} in {pa} states an exclusion the other does not.")
            elif partial_diff:
                rel, why, conf = RelationshipType.CONTEXTUALLY_EXPLAINED, "PARTIAL_PERIOD", 0.8
                summary = (f"Apparent conflict explained by coverage: one figure covers only part of {pa} "
                           f"({a.raw_value} vs {b.raw_value}).")
            elif (a.document_id == b.document_id
                  and a.source_evidence.char_start is not None
                  and a.source_evidence.char_start == b.source_evidence.char_start
                  and a.source_evidence.page_number == b.source_evidence.page_number):
                # both numbers were read out of the same sentence, so they almost certainly
                # describe different things the extractor could not tell apart
                rel, why, conf = RelationshipType.INSUFFICIENT_EVIDENCE, "SAME_SENTENCE", 0.5
                summary = (f"{a.raw_value} and {b.raw_value} were both read from one sentence in {a.document_name}, "
                           f"so they are probably different figures rather than a conflict. Needs review.")
            elif a.document_id == b.document_id:
                # One report rarely contradicts itself outright; two different figures under the same
                # label almost always mean a segment, a subset or a differently defined series that
                # neither the label nor the surrounding context spelled out. Saying "needs review"
                # is the honest verdict - a confident CONTRADICTION here would usually be wrong.
                rel, why, conf = RelationshipType.INSUFFICIENT_EVIDENCE, "SAME_LABEL_DIFFERENT_SUBJECTS", 0.5
                detail = ("the label was read from the sentences, so these are probably about different subjects"
                          if a.metric_canonical.startswith("metric.")
                          else "within one report this usually means a segment, a subset or a differently defined figure")
                summary = (f"'{a.metric}' appears twice in {a.document_name} for {pa} with different values "
                           f"({a.raw_value} vs {b.raw_value}); {detail}. Needs review.")
            elif family_only:
                rel, why, conf = RelationshipType.INSUFFICIENT_EVIDENCE, "FAMILY_MATCH_ONLY", 0.45
                summary = (f"'{a.metric}' ({a.raw_value}) and '{b.metric}' ({b.raw_value}) describe related "
                           f"quantities for {pa}, but they are different labels grouped by similarity rather "
                           f"than the same measurement. Values differ; this needs review, not a conflict call.")
            else:
                both_estimates = "estimate" in qa and "estimate" in qb
                same_doc = a.document_id == b.document_id
                rel = RelationshipType.CONTRADICTION
                why = "COMPETING_ESTIMATES" if both_estimates else "VALUE_CONFLICT"
                conf = 0.7 if both_estimates else (0.65 if same_doc else 0.85)
                summary = ((f"Likely contradiction: two estimates for {a.metric} in {pa} disagree "
                            f"({a.raw_value} vs {b.raw_value}).") if both_estimates else
                           (f"Contradiction: same entity, metric ({a.metric}), period ({pa}) and context, but the values "
                            f"differ: {a.raw_value} vs {b.raw_value} ({how})." +
                            (" Both come from the same document, so one may be a subset or a differently defined figure." if same_doc else "")))
            return FactRelationship(
                fact_a_id=a.id, fact_b_id=b.id, relationship_type=rel, confidence=round(conf * min(a.confidence, b.confidence) / 0.9, 2),
                reasoning_trace=ReasoningTrace(checks=checks, summary=summary, primary_divergence_factor=why, confidence=conf),
                entity_canonical=a.entity_canonical, metric_canonical=a.metric_canonical,
                delta_percentage=round(delta_pct, 2), delta_absolute=round(delta_abs, 4), created_at=now,
                match_basis=match_basis)

        # text / status facts ---------------------------------------------------------
        ta, tb = a.raw_value.lower().strip(), b.raw_value.lower().strip()
        # A value that is a single unbroken token (an identifier) is not prose: token-set Jaccard
        # degenerates to 0/1 for it, so compare those by character-level ratio instead. Everything
        # else - addresses, names, status words - keeps the existing token-set metric untouched.
        identifier_pair = is_identifier_shaped(ta) and is_identifier_shaped(tb)
        if identifier_pair:
            sim = identifier_similarity(ta, tb)
            same_text = ta == tb  # for an identifier, "close" is not "same" - that's a revision, below
        else:
            sim = text_similarity(ta, tb)
            same_text = ta == tb or sim >= 0.6
        checks.append(ReasoningCheck(step_number=5, name="Text", passed=same_text,
                                     explanation=(f"'{a.raw_value}' vs '{b.raw_value}'" + ("" if ta == tb else
                                                  f" ({'character' if identifier_pair else 'token overlap'} similarity {sim:.0%})")),
                                     details={"similarity": round(sim, 2)}))
        attribute = a.metric_canonical.startswith("attr.")
        if attribute:
            if same_text:
                rel, why, conf = RelationshipType.CORROBORATED, None, 0.9 if ta == tb else 0.75
                summary = (f"Both documents give the same {a.metric} for {a.entity}" +
                           ("" if ta == tb else f", written differently ('{a.raw_value}' vs '{b.raw_value}')") + ".")
            elif identifier_pair and sim >= IDENTIFIER_REVISION_SIMILARITY:
                # Two identifier-shaped values that are close but not equal: the honest reading is
                # that the identifier was revised between documents, not that the sources disagree.
                rel, why, conf = RelationshipType.CONTEXTUALLY_EXPLAINED, "IDENTIFIER_REVISED", 0.85
                # count on the same (lower-cased) strings the similarity ratio was computed from,
                # so the reported N/M matches sim exactly
                diff_chars, diff_total = char_diff_summary(ta, tb)
                da = a.period.raw or a.period.canonical
                db = b.period.raw or b.period.canonical
                summary = (f"Not a contradiction: {a.metric} for {a.entity} is '{a.raw_value}' in {a.document_name}"
                           f"{f' ({da})' if da and da != 'unknown' else ''} and '{b.raw_value}' in {b.document_name}"
                           f"{f' ({db})' if db and db != 'unknown' else ''}; they differ in {diff_chars} character"
                           f"{'s' if diff_chars != 1 else ''} out of {diff_total}, so this looks like a revision of "
                           f"one identifier rather than two conflicting claims.")
            elif not identifier_pair and sim >= 0.3:
                rel, why, conf = RelationshipType.INSUFFICIENT_EVIDENCE, "PARTIAL_TEXT_MATCH", 0.5
                summary = (f"{a.metric} for {a.entity} partly overlaps between documents ('{a.raw_value}' vs '{b.raw_value}'); "
                           f"could be the same thing written differently or a real change. Needs review.")
            elif family_only:
                # Same policy as the numeric ladder: a family match is weaker evidence than an
                # exact key, so disagreement here is reported as uncertain, never a conflict.
                rel, why, conf = RelationshipType.INSUFFICIENT_EVIDENCE, "FAMILY_MATCH_ONLY", 0.45
                summary = (f"'{a.metric}' ('{a.raw_value}') and '{b.metric}' ('{b.raw_value}') for {a.entity} describe "
                           f"related attributes grouped by similarity rather than the same claim. Values differ; "
                           f"this needs review, not a conflict call.")
            else:
                rel, why, conf = RelationshipType.CONTRADICTION, "ATTRIBUTE_CONFLICT", 0.7
                if identifier_pair:
                    summary = (f"Contradiction: both sources identify {a.entity}, but report different {a.metric}s "
                               f"('{a.raw_value}' in {a.document_name} vs '{b.raw_value}' in {b.document_name}).")
                else:
                    summary = f"{a.metric} for {a.entity} differs between documents: '{a.raw_value}' vs '{b.raw_value}'."
            return FactRelationship(
                fact_a_id=a.id, fact_b_id=b.id, relationship_type=rel, confidence=round(conf * min(a.confidence, b.confidence) / 0.9, 2),
                reasoning_trace=ReasoningTrace(checks=checks, summary=summary, primary_divergence_factor=why, confidence=conf),
                entity_canonical=a.entity_canonical, metric_canonical=a.metric_canonical, created_at=now,
                match_basis=match_basis)
        if same_text and period_ok:
            rel, why, conf = RelationshipType.CORROBORATED, None, 0.9
            summary = f"Both documents state {a.entity}: {a.raw_value} as of {pa}."
        elif same_text:
            rel, why, conf = RelationshipType.CORROBORATED, None, 0.75
            summary = f"Both documents state {a.entity}: {a.raw_value} (dated {a.period.raw or pa} and {b.period.raw or pb})."
        elif not period_ok:
            rel, why, conf = RelationshipType.CONTEXTUALLY_EXPLAINED, "STATUS_CHANGED_OVER_TIME", 0.88
            first, later = (a, b) if pa <= pb else (b, a)
            summary = (f"Not a contradiction: {a.entity} was '{first.raw_value}' as of {first.period.raw or first.period.canonical} "
                       f"({first.document_name}) and '{later.raw_value}' as of {later.period.raw or later.period.canonical} "
                       f"({later.document_name}); the status changed over time.")
        elif family_only:
            # Same policy as the numeric ladder: a family match is weaker evidence than an
            # exact key, so disagreement here is reported as uncertain, never a conflict.
            rel, why, conf = RelationshipType.INSUFFICIENT_EVIDENCE, "FAMILY_MATCH_ONLY", 0.45
            summary = (f"'{a.metric}' ('{a.raw_value}') and '{b.metric}' ('{b.raw_value}') for {a.entity} describe "
                       f"related statuses grouped by similarity rather than the same claim for {pa}. Values differ; "
                       f"this needs review, not a conflict call.")
        else:
            rel, why, conf = RelationshipType.CONTRADICTION, "STATUS_CONFLICT", 0.8
            summary = f"Contradiction: {a.entity} is '{a.raw_value}' in one source and '{b.raw_value}' in another for the same date ({pa})."
        return FactRelationship(
            fact_a_id=a.id, fact_b_id=b.id, relationship_type=rel, confidence=round(conf * min(a.confidence, b.confidence) / 0.9, 2),
            reasoning_trace=ReasoningTrace(checks=checks, summary=summary, primary_divergence_factor=why, confidence=conf),
            entity_canonical=a.entity_canonical, metric_canonical=a.metric_canonical, created_at=now,
            match_basis=match_basis)
