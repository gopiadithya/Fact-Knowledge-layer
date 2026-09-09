"""
Fact extraction.

Deterministic and document-agnostic. Three sources of facts:

1. Sentences: a taxonomy metric keyword + a value of the right kind + a
   period, all inside one sentence. One sentence usually states several
   claims, so each value is bound to its own metric and its own period by
   reading the sentence's structure rather than raw proximity:

     "₹81,415 Mn for FY24 as against ₹72,253 Mn for FY23"
         -> two levels, each taking the period that follows it
     "FY24 EBITDA increased by ₹578 Cr to ₹127 Cr from ₹(452) Cr in FY23"
         -> EBITDA FY24 = 127, EBITDA FY23 = -452, EBITDA change FY24 = +578

   A change is stored under its own `<metric>.change` key, so the reasoner
   never compares a movement against a level.
2. Tiles: numeric block + adjacent label block on KPI pages / slides.
   Unknown labels are still recorded under a generated `metric.<slug>` key,
   so the schema grows as new kinds of facts show up.
3. Governance events: "<person> resigned/appointed ... with effect from <date>".

Every fact carries a confidence. Facts below `MATCH_THRESHOLD` are kept and
shown (they are the honest failure cases) but never enter relationship
matching.
"""
import re
from collections import Counter
from typing import List, Optional, Tuple

from ..models.document import Document, DocumentPage
from ..models.fact import Fact, FactContext, FactPeriod, MetricCategory, PeriodType, SourceEvidence
from .normalizer import (
    METRIC_BY_CANONICAL, MONTH_RE, MetricSpec, ParsedValue, detect_qualifiers, detect_scope,
    find_metric_mentions, find_periods, normalize_entity, normalize_metric, parse_values, prenormalize,
)

MATCH_THRESHOLD = 0.6   # facts below this are surfaced as low-confidence and excluded from matching

COUNTRIES = ["India", "United States", "China", "Japan", "Germany", "United Kingdom", "France", "Brazil", "Indonesia",
             "Singapore", "Australia", "Canada", "Bangladesh", "Sri Lanka", "Pakistan", "Nepal", "Euro area"]

INSTITUTIONS = ["Reserve Bank of India", "International Monetary Fund", "World Bank", "Ministry of Finance",
                "Government of India", "Asian Development Bank", "Securities and Exchange Board of India",
                "Department of Economic Affairs", "European Central Bank", "Federal Reserve"]

_CORP_NAME = re.compile(
    r"(?P<name>(?:[A-Z][A-Za-z0-9&'\-]*\.?\s+){1,5}(?:Limited|Ltd\.?|Inc\.?|Incorporated|Corporation|Corp\.?|PLC|LLC|LLP|Pvt\.?\s?Ltd\.?|Private Limited)(?![a-z]))"
)
_NAME_STOP = {"for", "the", "of", "by", "and", "with", "to", "in", "on", "at", "yours", "sincerely", "faithfully", "dear",
              "from", "our", "company", "limited", "ltd", "a", "an", "as", "or", "sub", "re"}
_NAME = r"(?:Mr\.|Ms\.|Mrs\.|Dr\.)?\s*(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})"
_DATE = r"(?P<date>(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+(?:19|20)\d\d)"
_GOV_CEASED = re.compile(
    rf"{_NAME}[^.]{{0,120}}?(?i:(?P<event>resigned|ceased to be|stepped down|retired))[^.]{{0,160}}?(?i:with effect from|w\.e\.f\.|effective|on)\s+{_DATE}")
_NAME2 = _NAME.replace("(?P<name>", "(?P<name2>")
_GOV_APPOINTED = re.compile(
    rf"(?:(?i:(?P<event1>appointment|appointed|re-appointed|reappointed|re-appointment))[^.]{{0,60}}?{_NAME}|{_NAME2}[^.]{{0,120}}?(?i:(?P<event2>was appointed|has been appointed|is appointed|appointed as|re-appointed as)))[^.]{{0,200}}?(?i:with effect from|w\.e\.f\.|effective|on|from)\s+{_DATE}")
_GOV_PROFILE = re.compile(
    rf"^{_NAME}\s+is\s+(?:the\s+|a\s+|an\s+)?(?P<role>[A-Z][A-Za-z\-, ]{{3,80}}?Director[A-Za-z\-, ]{{0,40}}?)\s+of\s+(?:our|the)\s+Company")
_ORG_WORD = re.compile(r"\b(bank|fund|ministry|department|government|institute|university|corporation|corp|council|"
                       r"authority|commission|agency|group|association|foundation|society|board|office|"
                       r"limited|ltd|inc|llc|llp|plc|company|co)\b", re.I)
_RUNNING_HEADER = re.compile(r"(?:[A-Z][A-Z&/'\-]{3,}\s+){2,6}[a-z]")
_PROPER_PHRASE = re.compile(r"\b(?:[A-Z][a-z]{2,}\s+){1,3}[A-Z][a-z]{2,}\b")
_HEADING_WORD = re.compile(r"\b(chapter|page|table|chart|box|figure|annex|appendix|contents|source|note|"
                           r"introduction|section|part|preface|abbreviations)\b", re.I)
_CONTENTS_LINE = re.compile(r"\b(?:page|chapter|table|figure|annex)\s*(?:no\.?|number)\b|\.{3,}|^contents\b", re.I)
_ROLE_WORDS = re.compile(r"\b(Company|Officer|Director|Board|Secretary|Deputy|Chief|Head|President|Managing|Executive|Independent|"
                         r"Nominee|Member|Committee|Limited|Post|Annual|General|Meeting|Financial|Year|Date|Report)\b")


_WINDOW_AFTER_PERIOD = re.compile(
    rf"^\W{{0,3}}\(?(?:{MONTH_RE}\s*(?:-|–|to)\s*{MONTH_RE}|h[12]\b|q[1-4]\b|first half|second half|"
    r"nine months|six months|three months)", re.I)


def _window_follows_period(sentence_lower: str, period_raw: str) -> bool:
    """True for "FY25 (April-December)": the window is written onto this value's own period.

    A partial-period marker anywhere else in the sentence belongs to a different claim, so only
    a marker attached to the period the value was bound to makes that value partial.
    """
    if not period_raw:
        return False
    at = sentence_lower.find(period_raw.lower())
    if at < 0:
        return False
    return bool(_WINDOW_AFTER_PERIOD.match(sentence_lower[at + len(period_raw):]))


def _slug_person(name: str) -> str:
    return normalize_entity(re.sub(r"^(Mr|Ms|Mrs|Dr)\.?\s+", "", name.strip()))


class FactExtractor:
    # ------------------------------------------------------------------ document context
    @classmethod
    def resolve_document_context(cls, document: Document) -> None:
        """Fill primary_entity / subject_country / document_date / document_kind on the metadata."""
        md = document.metadata
        head = "\n".join(p.text for p in document.pages[:3])
        full = "\n".join(p.text for p in document.pages)
        head_l, full_l = head.lower(), full.lower()

        # 1. corporate entity: the "<Name> Limited/Inc/..." string repeated most often across the document
        cands: Counter = Counter()
        for m in _CORP_NAME.finditer(full):
            words = re.sub(r"\s+", " ", m.group("name")).strip(" ,.").split(" ")
            while words and words[0].lower().strip(".,") in _NAME_STOP:
                words = words[1:]
            name = " ".join(words)
            if 4 < len(name) < 60 and not re.search(r"\b(annual report|prospectus|table|contents|offer|act|regulations?)\b", name, re.I):
                cands[name] += 1
        institution = next((i for i in INSTITUTIONS if i.lower() in head_l), None)
        top_name, top_n = cands.most_common(1)[0] if cands else (None, 0)
        # A report cites other companies far more often than it names itself in full, so a
        # corporate name that never appears in the opening pages loses to a publisher that does.
        named_up_front = bool(top_name) and top_name.lower() in head_l
        if institution and (not named_up_front or top_n < 3 or full_l.count(institution.lower()) >= top_n):
            md.primary_entity = institution
        elif top_name:
            md.primary_entity = top_name
        else:
            clean = re.sub(r"^[0-9]+-|\.pdf$", "", md.original_name, flags=re.I).replace("-", " ").replace("_", " ").title()
            md.primary_entity = (cls._entity_from_pdf_metadata(md)
                                 or cls._repeated_organisation(document)
                                 or cls._title_line(document)
                                 or (clean if len(clean) > 3 else "Unknown entity"))

        # 2. subject country for macro metrics
        counts = Counter({c: full_l.count(c.lower()) for c in COUNTRIES})
        top, n = counts.most_common(1)[0]
        md.subject_country = top if n >= 5 else None

        # 3. document date: cover date, else "Annual Report 2023-24"/"FY24" style, else any date on page 1
        m = re.search(r"\b(?:dated|as of|as on)\s+" + _DATE, head, re.I)
        if m:
            md.document_date = _iso(m.group("date"))
        else:
            ar = re.search(r"annual report\s+(20\d\d)\s?[-–]\s?(\d{2,4})", head_l)
            if ar:
                y2 = ar.group(2)
                year = int(y2) if len(y2) == 4 else int(ar.group(1)[:2] + y2)
                md.document_date = f"{year}-03-31"
            else:
                m = re.search(_DATE, head)
                md.document_date = _iso(m.group("date")) if m else None

        # 3b. document period: the fiscal period the document reports on (used when a KPI tile has no period label)
        md.document_period = None
        ar = re.search(r"annual report\s+(20\d\d)\s?[-–]\s?(\d{2,4})", head_l)
        fy_end = re.search(r"(?:financial\s+)?year\s+ended\s+" + _DATE, head, re.I)
        if ar:
            y2 = ar.group(2)
            md.document_period = f"FY{int(y2) if len(y2) == 4 else int(ar.group(1)[:2] + y2)}"
        elif fy_end and _iso(fy_end.group("date")):
            md.document_period = f"FY{int(_iso(fy_end.group('date'))[:4])}"
        else:
            fy = re.findall(r"\bFY\s?(\d{2,4})\b", head)
            if fy:
                y = Counter(fy).most_common(1)[0][0]
                md.document_period = f"FY{2000 + int(y) if len(y) == 2 else int(y)}"

        # 4. document kind
        if "prospectus" in head_l:
            md.document_kind = "prospectus"
        elif "annual report" in head_l:
            md.document_kind = "annual_report"
        elif re.search(r"\b(earnings|investor|results)\b.*\b(presentation|update)\b", head_l) or md.page_count < 40 and "q4" in head_l:
            md.document_kind = "presentation"
        elif institution or re.search(r"economic survey|article iv|staff report", head_l) or full_l.count("economic survey") >= 3:
            md.document_kind = "macro_report"
        else:
            md.document_kind = "unknown"


    # ------------------------------------------------------------------ entity fallbacks
    @staticmethod
    def _entity_from_pdf_metadata(md) -> Optional[str]:
        """The PDF's own author field, when it names an organisation rather than a person."""
        author = (md.pdf_author or "").strip()
        if not (2 <= len(author) <= 60):
            return None
        if len(author.split()) == 1 or _ORG_WORD.search(author):
            return author
        return None

    @staticmethod
    def _repeated_organisation(document: Document) -> Optional[str]:
        """The capitalised name the document repeats across its pages.

        Used only when nothing named an entity outright: an excerpt with no cover page still
        refers to its own publisher ("Economic Survey", "Reserve Bank") on page after page,
        which is a better answer than the first heading that happens to sit on page one.
        """
        total: Counter = Counter()
        pages_with: Counter = Counter()
        for page in document.pages:
            seen = set()
            for m in _PROPER_PHRASE.finditer(page.text):
                phrase = re.sub(r"\s+", " ", m.group(0)).strip()
                if _HEADING_WORD.search(phrase) or _ROLE_WORDS.search(phrase):
                    continue
                total[phrase] += 1
                seen.add(phrase)
            for phrase in seen:
                pages_with[phrase] += 1
        ranked = sorted(((n, len(p), p) for p, n in total.items() if n >= 4 and pages_with[p] >= 3), reverse=True)
        return ranked[0][2] if ranked else None

    @staticmethod
    def _title_line(document: Document) -> Optional[str]:
        """The first title-like line on page one, skipping contents-table furniture."""
        for line in (document.pages[0].text if document.pages else "").split("\n"):
            line = line.strip()
            if not (3 <= len(line.split()) <= 12) or not re.search(r"[A-Za-z]{3}", line):
                continue
            if re.search(r"\d{3,}", line) or "\t" in line or _CONTENTS_LINE.search(line):
                continue
            return line
        return None

    # ------------------------------------------------------------------ entry point
    @classmethod
    def extract_from_document(cls, document: Document) -> List[Fact]:
        cls.resolve_document_context(document)
        md = document.metadata
        facts: List[Fact] = []
        for page in document.pages:
            taxonomy_facts = cls._extract_sentences(document, page)
            covered = {(f.source_evidence.char_start, f.source_evidence.char_end) for f in taxonomy_facts}
            facts.extend(taxonomy_facts)
            facts.extend(cls._extract_open_numeric(document, page, covered))
            facts.extend(cls._extract_attributes(document, page))
            facts.extend(cls._extract_tiles(document, page))
            facts.extend(cls._extract_governance(document, page))
        return cls._dedupe(facts)

    # ------------------------------------------------------------------ helpers
    @classmethod
    def _subject_for(cls, spec: Optional[MetricSpec], md) -> Tuple[str, str]:
        if spec is not None and spec.subject == "country" and md.subject_country:
            return md.subject_country, normalize_entity(md.subject_country)
        return md.primary_entity or "Unknown entity", normalize_entity(md.primary_entity or "unknown")

    @staticmethod
    def _value_compatible(spec: MetricSpec, v: ParsedValue, keyword_pos: Optional[int] = None) -> bool:
        if spec.value_kind == "money":
            return v.kind == "money"
        if spec.value_kind == "percent":
            return v.kind == "percent"
        if spec.value_kind == "count":
            if v.kind != "count":
                return False
            if spec.count_unit == "TONNES":
                return v.normalized_unit == "TONNES"
            if v.normalized_unit != "COUNT":
                return False
            if v.count_word:
                return v.count_word.lower() in spec.count_words if spec.count_words else True
            # a bare number only counts when it sits close to the metric keyword
            return keyword_pos is None or abs(v.start - keyword_pos) <= 60
        return False

    # cue words that introduce each part of a period-over-period statement
    _CUE_DELTA_BY = re.compile(r"\bby\s*$")
    _CUE_DELTA_OF = re.compile(r"\b(?:increase|decrease|growth|decline|reduction|rise|fall|drop|"
                               r"improvement|expansion|addition)\s+of\s*$")
    _CUE_TO = re.compile(r"\bto\s*$")
    _CUE_FROM = re.compile(r"\b(?:from|against|versus|vs\.?)\s*$")
    _CHANGE_VERB = re.compile(r"\b(increas|decreas|grew|grow|rose|risen|fell|fall|declin|reduc|improv|expand|"
                              r"contract|up|down|higher|lower|widen|narrow)")

    _CUE_COMPARISON = re.compile(r"\b(?:to|from|against|versus|vs\.?|by)\s*$")
    # a partitive phrase in front of the metric means the figure is a slice of a larger whole
    _CUE_PARTITIVE = re.compile(r"\b(?:share|portion|part|proportion)\s+of\s*$")

    @classmethod
    def _inherit_elided_scale(cls, lower: str, values) -> set:
        """"... increased by ₹7,585.93 million from ₹(10,077.79) million in FY23 to ₹(2,491.86) in
        FY24" writes the scale word once and leaves it implicit on the last figure. Without this the
        final amount is read as ₹2,491.86 rather than ₹2.49 billion, and the same fact stated twice
        in one report looks like a contradiction.

        Only a bare amount introduced by a comparison cue inherits, and only when every scaled
        amount in the sentence agrees on the scale - "₹500 million of revenue and a ₹5 fee" must
        not pick anything up. Returns the set of value start offsets that were adjusted.
        """
        money = [v for v in values if v.kind == "money"]
        scaled = [v for v in money if v.normalized != v.numeric]
        bare = [v for v in money if v.normalized == v.numeric]
        if not scaled or not bare:
            return set()
        # scale factors are whole powers of ten; round away the division's float noise
        factors = {round(v.normalized / v.numeric) for v in scaled if v.numeric}
        if len(factors) != 1:
            return set()
        factor = factors.pop()
        unit = scaled[0].unit
        adjusted = set()
        for v in bare:
            if not cls._CUE_COMPARISON.search(lower[max(0, v.start - 24): v.start]):
                continue
            v.normalized = round(v.numeric * factor, 6)
            v.unit = unit
            adjusted.add(v.start)
        return adjusted

    @staticmethod
    def _value_owners(values, mentions) -> dict:
        """Map each value to the metric mention it sits nearest to, so one sentence's clauses do
        not all get filed under whichever metric happens to be named first."""
        def distance(m, v):
            # "EBITDA ... Rs. (452 Cr) in FY23 b PAT ..." - the metric named *before* an amount owns
            # it; a metric that only appears afterwards is a much weaker claim, so it pays a penalty
            # and wins only when nothing precedes the value.
            return (v.start - m[1]) if m[1] <= v.start else (m[0] - v.end) + 40

        owners = {}
        for v in values:
            best = min(mentions, key=lambda m: distance(m, v), default=None)
            if best is not None:
                owners[v.start] = best[0]
        return owners

    @classmethod
    def _clause_bindings(cls, text: str, lower: str, values, periods, mention_start: int,
                         spec: MetricSpec) -> dict:
        """Read a comparison sentence the way a person does.

        "FY24 EBITDA increased by Rs. 578 Cr to Rs. 127 Cr from Rs. (452 Cr) in FY23" states three
        things, and the cue word in front of each amount says which:

            by <x>    -> the change between the two periods
            to <x>    -> the new level, which belongs to the subject period ("FY24" before "EBITDA")
            from <x>  -> the old level, which belongs to the period named right after it ("in FY23")

        Returns {value.start: (period, "level" | "delta", period_token_start)} for the values whose
        role the sentence makes explicit. Everything else is left to the proximity pass.
        """
        out: dict = {}
        if not periods:
            return out
        # the subject period sits immediately before the metric it qualifies: "FY24 EBITDA ...".
        # Only whitespace may separate them - in "... profitable in Q3 FY24; PAT loss reduced ..."
        # the Q3 belongs to the clause before the semicolon, not to PAT.
        subject = subject_start = None
        for a, b, p in periods:
            if 0 <= mention_start - b <= 3 and not text[b: mention_start].strip():
                subject, subject_start = p, a
                break
        has_change_verb = bool(cls._CHANGE_VERB.search(lower))
        # for a metric that is itself a rate of change ("GDP growth", "inflation"), "growth of 6.5%"
        # states the level, not a delta on top of it
        is_rate_metric = any(w in spec.label.lower() for w in ("growth", "inflation", "rate"))

        from_period = None
        for v in values:
            before = lower[max(0, v.start - 24): v.start]
            if not cls._CUE_FROM.search(before):
                continue
            # "from Rs. (452 Cr) in FY23" - the period that follows the old value, close by
            following = [(a, p) for a, b, p in periods if 0 <= a - v.end <= 30]
            if following:
                out[v.start] = (following[0][1], "level", following[0][0])
                from_period = following[0][1]

        for v in values:
            before = lower[max(0, v.start - 24): v.start]
            if v.start in out or not has_change_verb:
                continue
            is_delta = not is_rate_metric and (cls._CUE_DELTA_OF.search(before)
                                               or (cls._CUE_DELTA_BY.search(before)
                                                   and cls._CHANGE_VERB.search(lower[max(0, v.start - 60): v.start])))
            if is_delta:
                # a change belongs to the period it lands in: the stated subject period, or failing
                # that the year after the one it moved "from"
                out[v.start] = (subject or _next_fiscal_year(from_period), "delta", None)
            elif cls._CUE_TO.search(before) and subject is not None:
                out[v.start] = (subject, "level", subject_start)
        return out

    @classmethod
    def _make_fact(cls, document: Document, page: DocumentPage, spec: Optional[MetricSpec], metric_label: str,
                   value: ParsedValue, period: FactPeriod, quote: str, start: int, end: int,
                   confidence: float, sentence_lower: str, method: str, notes: Optional[str] = None,
                   value_pos: Optional[int] = None, canonical_suffix: str = "", mention_text: str = "",
                   extra_qualifiers: Tuple[str, ...] = ()) -> Fact:
        md = document.metadata
        entity, entity_c = cls._subject_for(spec, md)
        if spec:
            canonical, category = spec.canonical, spec.category
        else:
            canonical, category = normalize_metric(metric_label)
        # a change ("EBITDA increased by ₹578 Cr") is a different quantity from the level it moves;
        # giving it its own canonical keeps the reasoner from comparing the two
        canonical += canonical_suffix
        # sign: "loss" metrics reported as positive numbers are negative economically
        numeric, normalized = value.numeric, value.normalized
        local = sentence_lower[max(0, (value_pos or 0) - 80): (value_pos or 0) + len(value.raw) + 80] if value_pos is not None else sentence_lower
        before = sentence_lower[max(0, (value_pos or 0) - 16): value_pos] if value_pos is not None else ""
        # "PAT loss reduced by ₹759 Cr" is a positive movement: the sign of a level that happens to
        # be a loss does not carry over to the change in that level
        metric_is_loss = (spec and spec.sign_negative_if and not canonical_suffix
                          and any(w in local for w in spec.sign_negative_if)
                          and not re.search(r"\bprofit of\s*$", before))
        if numeric > 0 and (re.search(r"\b(loss of|negative|deficit of|\(loss\))\s*$", before) or metric_is_loss):
            numeric, normalized = -numeric, -normalized
        # "Our Adjusted EBITDA margin has improved from (11.35%) ... to (6.95%) in Fiscal 2021" states
        # the basis once, at the head of the sentence and out of reach of the window around the
        # value. The matched keyword itself carries it, so trust that first.
        non_gaap = bool(spec and spec.non_gaap_if
                        and any(w in local or w in mention_text for w in spec.non_gaap_if))
        qualifiers = detect_qualifiers(local)
        # "partial_period" is detected sentence-wide, but a sentence can carry claims about several
        # periods ("... in FY23 ... PAT profitable in Q3 FY24"). A fact whose own period is a full
        # fiscal year is not partial just because a quarter is named elsewhere in the same sentence.
        if (period.period_type == PeriodType.FISCAL_YEAR and "partial_period" in qualifiers
                and not _window_follows_period(sentence_lower, period.raw)):
            qualifiers.remove("partial_period")
        for q in extra_qualifiers:
            if q not in qualifiers:
                qualifiers.append(q)
        if non_gaap:
            qualifiers.append("adjusted_non_gaap")
        return Fact(
            document_id=md.id, document_name=md.original_name,
            entity=entity, entity_canonical=entity_c,
            metric=metric_label, metric_canonical=canonical, metric_category=category,
            raw_value=value.raw, numeric_value=numeric, normalized_numeric_value=normalized,
            unit=value.unit, normalized_unit=value.normalized_unit,
            period=period,
            context=FactContext(scope=detect_scope(sentence_lower),
                                accounting_standard="Non-GAAP (adjusted)" if non_gaap else "As reported",
                                qualifiers=qualifiers),
            source_evidence=SourceEvidence(document_id=md.id, document_name=md.original_name,
                                           page_number=page.page_number, exact_quote=quote,
                                           context_window=quote, char_start=start, char_end=end),
            confidence=round(min(0.99, max(0.05, confidence)), 2), extraction_method=method, notes=notes,
        )

    # ------------------------------------------------------------------ 1. sentences
    @classmethod
    def _extract_sentences(cls, document: Document, page: DocumentPage) -> List[Fact]:
        facts: List[Fact] = []
        for sent in page.sentences:
            text = prenormalize(sent.text)
            lower = text.lower()
            mentions = find_metric_mentions(lower)
            if not mentions:
                continue
            periods = find_periods(text)
            values = parse_values(text, [(a, b) for a, b, _ in periods])
            if not values or len(values) >= 7 or "...." in text:      # table rows and table-of-contents lines
                continue
            scale_inherited = cls._inherit_elided_scale(lower, values)
            # A chart or a second column splits a sentence in two; reflow then glues the tail onto
            # the page's running header. The tail parses as a sentence but has lost the subject its
            # number belonged to, so nothing extracted from it can be trusted enough to match.
            header_fragment = bool(_RUNNING_HEADER.match(text))
            industry = bool(re.search(r"\b(sector|industry|market size|addressable market)\b", lower))
            used_values = set()
            # Each value belongs to the metric mention it sits closest to. Without this the first
            # mention claims every compatible value in the sentence, so "... EBITDA increased ... ;
            # PAT loss reduced by Rs. 759 Cr" would file the PAT figures under EBITDA.
            owner = cls._value_owners(values, mentions)
            for m_start, m_end, spec, kw in mentions:
                compatible = [v for v in values if owner.get(v.start) == m_start
                              and cls._value_compatible(spec, v, m_end) and v.start not in used_values]
                if not compatible:
                    continue
                # grammar beats proximity: in "<period> M increased by <delta> to <new> from <old> in
                # <period>", the cue word in front of each value says which period it belongs to
                clause = cls._clause_bindings(text, lower, compatible, periods, m_start, spec)
                # "the Group's share of net profit of ₹86.95 million" is a slice of someone else's
                # profit, not this entity's own. Partitive grammar, so it holds for any metric.
                partitive = ("share_of",) if cls._CUE_PARTITIVE.search(lower[max(0, m_start - 30): m_start]) else ()
                # pair values with periods by proximity, each period claimed at most once
                # ("₹X Mn for FY24 as against ₹Y Mn for FY23" -> X:FY24, Y:FY23)
                # values the clause structure already resolved keep their period, and the period
                # tokens they consumed are off the table for the proximity pass below
                assigned: dict = {vs: p for vs, (p, kind, _) in clause.items() if p is not None}
                used_periods = {ps for (p, kind, ps) in clause.values() if p is not None and ps is not None}
                levels = [v for v in compatible if clause.get(v.start, (None, "level", None))[1] == "level"]
                pairs = []
                for v in levels:
                    if v.start in assigned:
                        continue
                    for (a, b, p) in periods:
                        # a period that follows the value ("4.6 per cent during 2024-25") binds tighter than one before it
                        gap = (a - v.end) if a >= v.end else (v.start - b) + 12
                        if 0 <= gap <= 57:
                            pairs.append((gap, v.start, a, v, p))
                for gap, vs, ps, v, p in sorted(pairs, key=lambda t: (t[0], t[1])):
                    if vs in assigned or ps in used_periods:
                        continue
                    assigned[vs] = p
                    used_periods.add(ps)
                paired = [(v, assigned.get(v.start), abs(v.start - m_end)) for v in levels]
                with_period = [t for t in paired if t[1] is not None]
                if with_period:
                    chosen = with_period                          # values with a period right next to them win
                elif paired:
                    nearest = min(paired, key=lambda t: t[2])     # else the value nearest the keyword
                    period = periods[0][2] if periods else None
                    chosen = [(nearest[0], period, nearest[2])]
                else:
                    chosen = []
                # a change ("increased by ₹578 Cr") is recorded as its own metric, never as a level
                chosen = [(v, p, d, "level") for v, p, d in chosen]
                chosen += [(v, clause[v.start][0], abs(v.start - m_end), "delta")
                           for v in compatible
                           if clause.get(v.start, (None, "level", None))[1] == "delta" and v.start not in used_values]
                for v, period, dist, value_kind in chosen:
                    used_values.add(v.start)
                    conf = 0.9
                    notes = None
                    if period is None:
                        period = FactPeriod(raw="", period_type=PeriodType.UNKNOWN, canonical="unknown")
                        conf -= 0.35
                        notes = "No reporting period found in the sentence."
                    elif v.start not in assigned:
                        conf -= 0.15
                        notes = "Period taken from elsewhere in the sentence, not next to the value."
                    after_v = lower[v.end: v.end + 30]
                    if re.search(r"^\s*(a year ago|a year earlier|in the previous year|in the corresponding period|year earlier)", after_v):
                        m_fy = re.fullmatch(r"FY(\d{4})", period.canonical)
                        if m_fy:
                            y = int(m_fy.group(1)) - 1
                            period = FactPeriod(raw=f"{period.raw} (a year earlier)", period_type=PeriodType.FISCAL_YEAR,
                                                canonical=f"FY{y}", start_date=f"{y - 1}-04-01", end_date=f"{y}-03-31")
                        else:
                            period = FactPeriod(raw="", period_type=PeriodType.UNKNOWN, canonical="unknown")
                        conf -= 0.1
                        notes = (notes + " " if notes else "") + "Value refers to the prior-year comparison."
                    if dist > 80:
                        conf -= 0.15
                    if v.start < m_start and m_start - v.end > 60:
                        conf -= 0.2
                        notes = (notes + " " if notes else "") + "Metric name appears well after the value; the value may belong to a different subject."
                    if len(values) >= 4:
                        conf -= 0.1     # dense numeric sentences are easy to mis-pair
                    if v.start in scale_inherited:
                        conf -= 0.05
                        notes = (notes + " " if notes else "") + f"Scale ({v.unit}) taken from the other amounts in the sentence."
                    elif spec.value_kind == "money" and v.unit == v.normalized_unit:
                        conf -= 0.3     # currency but no scale word: table figures in "₹ million" lose their scale
                        notes = (notes + " " if notes else "") + "No scale word (million/crore) next to the amount."
                    if industry and spec.subject == "company":
                        conf -= 0.35
                        notes = (notes + " " if notes else "") + "Sentence describes an industry/market figure, not the reporting entity."
                    window = lower[max(0, v.start - 140): v.end + 25]
                    near = [w for w in spec.exclude_near if w in window]
                    if near:
                        conf -= 0.35
                        notes = (notes + " " if notes else "") + f"Value sits next to '{near[0]}', so it is probably a component or a different series."
                    quals = detect_qualifiers(lower)
                    if "rate_or_capacity" in quals or "cumulative" in quals:
                        conf -= 0.3
                        notes = (notes + " " if notes else "") + "Figure is a rate/capacity or a cumulative total, not a period value."
                    before = lower[max(0, v.start - 14): v.start]
                    is_rate_metric = any(w in spec.label.lower() for w in ("growth", "inflation", "rate"))
                    is_delta_unit = bool(re.search(r"\b(bps|pp|percentage points?)\b", v.raw, re.I))
                    if re.search(r"\b(below|above|under|over|exceed(?:ed|ing)?|beyond|within)\s*$", before):
                        conf -= 0.25
                        notes = (notes + " " if notes else "") + "Figure is a threshold the text compares against, not the reported level."
                    if value_kind == "delta":
                        notes = (notes + " " if notes else "") + "Period-over-period change, not the level of the metric."
                    elif (is_delta_unit or not is_rate_metric) and re.search(r"\b(by|up|down|grew|rose|fell|declined|eased|increase of|decrease of|growth of)\s*$", before):
                        conf -= 0.35
                        notes = (notes + " " if notes else "") + "Figure is a change (\"by ...\"), not the level of the metric."
                    if spec.value_kind == "count":
                        after = lower[v.end: v.end + 30]
                        before40 = lower[max(0, v.start - 40): v.start]
                        activity = re.search(r"^\s*(who\s+)?(were|was|have|has|had|train|promot|receiv|moved|particip|engag|took|attend|complet|join|hired|recruit|underw|benefit|hold|spread|across)", after)
                        subset = re.search(r"\b(female|women|male|men|new|contractual|temporary|differently|disabled|senior|junior|trained|active|holding|attended|organised|organized|top|largest)\b", before40)
                        weak = kw in spec.weak_keywords
                        stock_phrase = re.search(r"\b(had|were|was|is|are|of|at|to|total|totalled|totaled|over|approximately|about|around|more than|stood at|strength of)\s*$", lower[max(0, v.start - 16): v.start])
                        if activity or subset or (weak and not stock_phrase):
                            conf -= 0.35
                            notes = (notes + " " if notes else "") + "Counts a subset or an activity (people trained, promoted, ...), not the stock figure."
                    if header_fragment:
                        conf -= 0.35
                        notes = ((notes + " " if notes else "")
                                 + "Sentence starts with a running header, so it is a fragment stitched across a "
                                   "chart or column break and the value may belong to a different subject.")
                    label = f"{spec.label} change" if value_kind == "delta" else spec.label
                    facts.append(cls._make_fact(document, page, spec, label, v, period, sent.text,
                                                sent.start, sent.end, conf, lower, "rule_sentence", notes,
                                                value_pos=v.start, mention_text=kw, extra_qualifiers=partitive,
                                                canonical_suffix=".change" if value_kind == "delta" else ""))
        return facts

    # ------------------------------------------------------------------ 1b. open-schema numeric statements
    _LABEL_VERB = re.compile(
        r"(?P<label>(?:[A-Za-z][\w'&/\-]*\s+){0,6}[A-Za-z][\w'&/\-]*)\s+"
        r"(?:(?:was|were|is|are|stood at|stands at|reached|totalled|totaled|amounted to|amounting to|came in at|"
        r"grew to|increased to|rose to|decreased to|declined to|fell to|reduced to|improved to|expanded to|of|at)\s+)"
        r"(?:approximately|approx\.|about|around|over|nearly|almost|more than|less than|~)?\s*$", re.I)
    _LABEL_VERBS = {"received", "receive", "had", "has", "have", "holds", "hold", "held", "implying", "growing", "entitled",
                    "approved", "estimated", "filed", "amounting", "witnesses", "operated", "delivered", "paid", "pay",
                    "recorded", "incurred", "spent", "invested", "raised", "issued", "granted", "declared", "proposed",
                    "expected", "projected", "reported", "represents", "representing", "comprising", "including", "being"}
    _LABEL_STOP = {"the", "a", "an", "our", "its", "their", "his", "her", "this", "that", "these", "those", "which", "who",
                   "and", "or", "of", "in", "on", "for", "to", "by", "with", "from", "as", "at", "we", "it", "company",
                   "company's", "group", "year", "period", "fiscal", "fy", "total", "respectively", "same", "previous",
                   "corresponding", "there", "also", "further", "however", "while", "during", "against", "than"}

    @classmethod
    def _extract_open_numeric(cls, document: Document, page: DocumentPage, covered: set) -> List[Fact]:
        """Facts whose metric label is not in the taxonomy: '<label> <verb> <value> [period]'.

        Only unit-bearing values are considered (currency, %, scale word or count noun), the label must contain a
        real word, and the label's head noun becomes a generated metric key. This is how new kinds of facts
        enter the layer from documents the taxonomy was never written for.
        """
        facts: List[Fact] = []
        for sent in page.sentences:
            if (sent.start, sent.end) in covered:
                continue
            text = prenormalize(sent.text)
            lower = text.lower()
            periods = find_periods(text)
            values = [v for v in parse_values(text, [(a, b) for a, b, _ in periods])
                      if v.unit not in ("count", "") or v.count_word]
            if not values or len(values) >= 5:
                continue
            for v in values[:2]:
                m = cls._LABEL_VERB.search(text[max(0, v.start - 90): v.start])
                if not m:
                    continue
                if "...." in text:
                    continue
                words = [w for w in re.findall(r"[A-Za-z][\w'&/\-]*", m.group("label"))]
                # keep only the noun phrase after the last verb / clause starter
                cut = max([i for i, w in enumerate(words) if w.lower() in cls._LABEL_VERBS or w.lower() in
                           ("we", "he", "she", "they", "it", "which", "that", "who", "and", "but", "as", "with", "from", "by")] + [-1])
                words = words[cut + 1:]
                while words and words[0].lower() in cls._LABEL_STOP:
                    words = words[1:]
                words = words[-5:]
                if (not words or len(words) < 1 or not any(len(w) >= 4 and w.lower() not in cls._LABEL_STOP for w in words)
                        or words[-1].lower() in ("of", "at", "in", "to", "for", "on", "by", "from")):
                    continue
                label = " ".join(words)
                # a period inside the label ("the loss for FY24 stood") belongs to the period field, not the name
                for a, b, _ in reversed(find_periods(label)):
                    label = (label[:a] + label[b:]).strip()
                label = re.sub(r"\s{2,}", " ", label)
                label = re.sub(r"(?i)^(?:the|a|an|our|its|their|whereas|and|but|for|of|in|on|at|to)\s+", "", label).strip()
                for _ in range(4):   # "the loss for FY24 stood" -> "loss"
                    trimmed = re.sub(r"(?i)\s+(?:for|of|in|on|at|to|from|by|as|with|the|a|an|stood|was|were|is|are|and|or)$", "", label).strip()
                    if trimmed == label:
                        break
                    label = trimmed
                if len(label) < 3:
                    continue
                # "FY24", "Q3 FY24", "March 2023" name a period, not a metric
                if find_periods(label) and not re.search(r"[A-Za-z]{3}", re.sub(r"(?i)\b(fy|cy|q[1-4]|h[12]|fiscal|financial|year|month|quarter|ended|ending)\b|\d", "", label)):
                    continue
                if v.count_word and v.count_word.lower() not in label.lower():
                    label = f"{label} ({v.count_word})"
                following = [p for (a, b, p) in periods if 0 <= a - v.end <= 45] or [p for (_, b, p) in periods if 0 <= v.start - b <= 45]
                period = following[0] if following else (periods[0][2] if periods else
                                                          FactPeriod(raw="", period_type=PeriodType.UNKNOWN, canonical="unknown"))
                conf = 0.68 if period.period_type != PeriodType.UNKNOWN else 0.4
                notes = "Open-schema fact: label taken from the sentence, not from the metric taxonomy."
                if period.period_type == PeriodType.UNKNOWN:
                    notes += " No reporting period found."
                facts.append(cls._make_fact(document, page, None, label, v, period, sent.text, sent.start, sent.end,
                                            conf, lower, "rule_open_schema", notes, value_pos=v.start))
                break
        return facts

    # ------------------------------------------------------------------ 1c. key-value attributes (any document)
    _KV_LINE = re.compile(r"^(?P<key>[A-Z][A-Za-z .&/()\-]{2,50}?)\s*[:\-–]\s+(?P<val>[^:]{3,160})$")
    _KV_STOP = {"date", "page", "tel", "telephone", "fax", "e-mail", "email", "website", "note", "notes", "source", "sources",
                "figure", "chart", "table", "sub", "ref", "re", "subject", "to", "from", "dear", "attn", "cc", "annexure",
                "section", "chapter", "contents", "particulars"}
    _ID_PATTERNS = [
        ("Corporate Identity Number (CIN)", re.compile(r"\b(?:CIN|Corporate Identity Number)\s*[:\-]?\s*([LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6})\b")),
        ("ISIN", re.compile(r"\bISIN\s*[:\-]?\s*(IN[A-Z0-9]{10})\b")),
        ("Registered office", re.compile(r"\bregistered\s+office\s+(?:is\s+)?(?:situated\s+|located\s+)?(?:at|on)\s+(?:leased\s+premises\s+at\s+)?([^.;]{12,180})", re.I)),
        ("Incorporation date", re.compile(rf"\b(?:incorporated|was incorporated)\s+(?:on|in)\s+{_DATE}", re.I)),
        ("Statutory auditor", re.compile(r"\b(?:statutory\s+)?auditors?\s*(?:of the company)?\s*[:,]?\s*(?:is|are|were|was|M/s\.?)\s+([A-Z][A-Za-z&.,' ]{4,60}?(?:LLP|& Co\.?|Associates|Chartered Accountants))", re.I)),
    ]

    @classmethod
    def _extract_attributes(cls, document: Document, page: DocumentPage) -> List[Fact]:
        facts: List[Fact] = []
        md = document.metadata
        entity, entity_c = md.primary_entity or "Unknown entity", normalize_entity(md.primary_entity or "unknown")
        seen = set()

        def add(label: str, value: str, quote: str, start: int, end: int, conf: float):
            value = re.sub(r"\s+", " ", value).strip(" ,;.")
            key = (label.lower(), value.lower())
            if key in seen or len(value) < 3:
                return
            seen.add(key)
            canonical = "attr." + re.sub(r"[^\w]+", "_", label.lower()).strip("_")[:50]
            facts.append(Fact(
                document_id=md.id, document_name=md.original_name, entity=entity, entity_canonical=entity_c,
                metric=label, metric_canonical=canonical, metric_category=MetricCategory.CORPORATE,
                raw_value=value, numeric_value=None, normalized_numeric_value=None, unit="", normalized_unit="TEXT",
                # These are identity/attribute facts (identifiers, registered address, ...). The only
                # date available here is the document's publication date from its metadata, not a
                # period stated next to the value, so the period is NOT_APPLICABLE rather than
                # POINT_IN_TIME - printing the document date as the fact's period would be misleading.
                period=FactPeriod(raw=md.document_date or "", period_type=PeriodType.NOT_APPLICABLE,
                                  canonical=md.document_date or "undated", source="document_metadata"),
                context=FactContext(scope="corporate", accounting_standard="n/a"),
                source_evidence=SourceEvidence(document_id=md.id, document_name=md.original_name, page_number=page.page_number,
                                               exact_quote=quote, context_window=quote, char_start=start, char_end=end),
                confidence=conf, extraction_method="rule_attribute",
            ))

        for label, pat in cls._ID_PATTERNS:
            for m in pat.finditer(page.text):
                val = m.group("date") if "date" in pat.groupindex else m.group(1)
                add(label, val, m.group(0), m.start(), m.end(), 0.85)
        if page.page_number <= 3:   # "Key: value" lines are meaningful on cover pages, not deep inside prose
            for line_m in re.finditer(r"[^\n]+", page.text):
                m = cls._KV_LINE.match(line_m.group(0).strip())
                if not m:
                    continue
                key = m.group("key").strip()
                if (key.lower() in cls._KV_STOP or len(key.split()) > 5 or re.search(r"\d", key) or "...." in m.group("val")
                        or re.match(r"(?i)^(section|annexure|chapter|part|schedule|appendix|exhibit|item)\b", key)):
                    continue
                add(key, m.group("val"), line_m.group(0).strip(), line_m.start(), line_m.end(), 0.7)
        return facts

    # ------------------------------------------------------------------ 2. tiles
    @classmethod
    def _extract_tiles(cls, document: Document, page: DocumentPage) -> List[Fact]:
        facts: List[Fact] = []
        for tile in page.tiles:
            label = tile.label_text
            label_l = label.lower()
            vtext = prenormalize(tile.value_text)
            values = parse_values(vtext)
            if not values:
                continue
            # a slash-separated tile ("₹127Cr / 1.6%") pairs with a slash-separated label ("EBITDA / EBITDA margin")
            label_parts = [p.strip() for p in re.split(r"\s/\s", label.split("FY")[0] if re.search(r"\bFY\d", label) else label)]
            label_parts = [p for p in label_parts if p] or [label]
            periods = find_periods(label)
            # the label may carry the prior-year comparison ("FY23: ₹(452) Cr"); the tile's own period is the page hint
            own = [p for _, _, p in periods if p.canonical == page.period_hint] if page.period_hint else []
            if own:
                period = own[0]
            elif periods and not re.search(r"FY\d{2,4}\s*:", label):
                period = periods[0][2]
            elif page.period_hint:
                period = FactPeriod(raw=page.period_hint, period_type=PeriodType.FISCAL_YEAR, canonical=page.period_hint)
            elif document.metadata.document_period:
                period = FactPeriod(raw=f"{document.metadata.document_period} (document period)",
                                    period_type=PeriodType.FISCAL_YEAR, canonical=document.metadata.document_period)
            else:
                period = FactPeriod(raw="", period_type=PeriodType.UNKNOWN, canonical="unknown")

            for idx, v in enumerate(values):
                part = label_parts[idx] if idx < len(label_parts) else label_parts[-1]
                part_l = part.lower()
                mentions = find_metric_mentions(part_l)
                spec = next((s for _, _, s, _ in mentions if cls._value_compatible(s, v)), None)
                conf = 0.85 if spec else 0.62
                notes = None
                if period.period_type == PeriodType.UNKNOWN:
                    conf -= 0.3
                    notes = "Tile has no period label and the page carries no period heading."
                elif "(document period)" in period.raw:
                    conf -= 0.1
                    notes = "Period inferred from the document's reporting period, not stated next to the tile."
                if spec is None:
                    notes = (notes + " " if notes else "") + "Label not in taxonomy; recorded under a generated metric key."
                metric_label = spec.label if spec else re.sub(r"\s+", " ", part).strip(" :")
                lower_ctx = f"{part_l} {vtext.lower()}"
                facts.append(cls._make_fact(document, page, spec, metric_label, v, period,
                                            f"{tile.value_text} — {label}", tile.start, tile.end, conf, lower_ctx,
                                            "rule_tile", notes))
        return facts

    # ------------------------------------------------------------------ 3. governance
    @classmethod
    def _extract_governance(cls, document: Document, page: DocumentPage) -> List[Fact]:
        facts: List[Fact] = []
        md = document.metadata
        seen = set()

        def add(name: str, status: str, date_iso: Optional[str], quote: str, start: int, end: int, conf: float, role: str = ""):
            name = re.sub(r"^(Mr|Ms|Mrs|Dr)\.?\s+", "", name.strip())
            key = (name, status, date_iso)
            if key in seen or len(name) < 5 or _ROLE_WORDS.search(name) or any(w in name.lower() for w in ("august", "march", "june", "april", "may ", "july")):
                return
            seen.add(key)
            period = (FactPeriod(raw=date_iso, period_type=PeriodType.POINT_IN_TIME, canonical=date_iso,
                                 start_date=date_iso, end_date=date_iso) if date_iso
                      else FactPeriod(raw="", period_type=PeriodType.UNKNOWN, canonical="unknown"))
            facts.append(Fact(
                document_id=md.id, document_name=md.original_name,
                entity=name, entity_canonical=_slug_person(name),
                metric="Board / executive status", metric_canonical="governance.board_status",
                metric_category=MetricCategory.GOVERNANCE,
                raw_value=status, numeric_value=None, normalized_numeric_value=None, unit="", normalized_unit="TEXT",
                period=period,
                context=FactContext(scope="corporate", accounting_standard="n/a", qualifiers=[role] if role else [],
                                    segment_name=md.primary_entity),
                source_evidence=SourceEvidence(document_id=md.id, document_name=md.original_name,
                                               page_number=page.page_number, exact_quote=quote, context_window=quote,
                                               char_start=start, char_end=end),
                confidence=conf, extraction_method="rule_governance",
            ))

        for sent in page.sentences:
            t = sent.text
            m = _GOV_CEASED.search(t)
            if m:
                add(m.group("name"), "ceased", _iso(m.group("date")), t, sent.start, sent.end, 0.88)
                continue
            m = _GOV_APPOINTED.search(t)
            if m:
                name = m.group("name") or m.group("name2")
                add(name, "appointed", _iso(m.group("date")), t, sent.start, sent.end, 0.85)
        # "X is a Non-Executive Nominee Director of our Company" (prospectus profiles): active as of the document date
        for sent in page.sentences:
            m = _GOV_PROFILE.match(sent.text)
            if m:
                role = re.sub(r"\s+", " ", m.group("role")).strip()
                add(m.group("name"), "active", md.document_date, sent.text, sent.start, sent.end,
                    0.8 if md.document_date else 0.5, role=role)
        return facts

    # ------------------------------------------------------------------ dedupe
    @staticmethod
    def _dedupe(facts: List[Fact]) -> List[Fact]:
        """Identical assertions repeated within one document collapse into one fact with an occurrence note."""
        keep: dict = {}
        for f in facts:
            key = (f.document_id, f.metric_canonical, f.entity_canonical, f.raw_value.lower() if f.numeric_value is None else round(f.normalized_numeric_value or 0, 6),
                   f.normalized_unit, f.period.canonical, f.context.scope, f.context.accounting_standard)
            if key in keep:
                first = keep[key]
                pages = first.notes or ""
                extra = f"p.{f.source_evidence.page_number}"
                if "Also stated on" in pages:
                    first.notes = pages + f", {extra}"
                else:
                    first.notes = (pages + " " if pages else "") + f"Also stated on {extra}"
                first.confidence = min(0.98, first.confidence + 0.02)
                continue
            keep[key] = f
        return list(keep.values())


_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _iso(date_text: str) -> Optional[str]:
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", date_text.strip())
    if not m:
        return None
    mo = _MONTHS.get(m.group(1)[:3].lower())
    return f"{int(m.group(3)):04d}-{mo:02d}-{int(m.group(2)):02d}" if mo else None


def _next_fiscal_year(period: Optional[FactPeriod]) -> Optional[FactPeriod]:
    """FY23 -> FY24. Used when a change is stated only as "reduced by X from Y in FY23"."""
    if period is None:
        return None
    m = re.fullmatch(r"FY(\d{4})", period.canonical)
    if not m:
        return None
    y = int(m.group(1)) + 1
    return FactPeriod(raw=f"FY{y % 100:02d} (year after {period.raw or period.canonical})",
                      period_type=PeriodType.FISCAL_YEAR, canonical=f"FY{y}",
                      start_date=f"{y - 1}-04-01", end_date=f"{y}-03-31")
