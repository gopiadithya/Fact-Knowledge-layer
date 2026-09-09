"""
Normalisation helpers: values/units, periods, entities and the metric taxonomy.

Everything here is deterministic. The taxonomy is a starting point, not a
closed schema: unknown metric labels fall through to a generated
`metric.<slug>` key so new kinds of facts can still enter the layer.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..models.fact import FactPeriod, MetricCategory, PeriodType

# ---------------------------------------------------------------------------
# Values and units
# ---------------------------------------------------------------------------

SCALE = {
    "crore": 1e7, "crores": 1e7, "cr": 1e7,
    "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
    "million": 1e6, "millions": 1e6, "mn": 1e6, "mm": 1e6, "m": 1e6,
    "billion": 1e9, "billions": 1e9, "bn": 1e9, "b": 1e9,
    "trillion": 1e12, "trillions": 1e12, "tn": 1e12,
    "thousand": 1e3, "thousands": 1e3, "k": 1e3,
}

CURRENCY_TOKENS = {
    "INR": [r"₹", r"\bRs\.?", r"\bINR\b", r"\brupees?\b", r"\bI\s(?=\d)"],  # 'I 650.02 million' is a PDF glyph artefact for ₹
    "USD": [r"\$", r"\bUSD\b", r"\bUS\$", r"\bdollars?\b"],
    "EUR": [r"€", r"\bEUR\b"],
    "GBP": [r"£", r"\bGBP\b"],
}

NUMBER = r"\(?-?\d{1,3}(?:,\d{2,3})+(?:\.\d+)?\)?|\(?-?\d+(?:\.\d+)?\)?"
SCALE_WORDS = r"(?:crores?|cr|lakhs?|lacs?|millions?|mn|mm|billions?|bn|trillions?|tn|thousands?|k)"
PCT_WORDS = r"(?:%|per\s?cent|percent|percentage\s+points?|pp|bps)"
COUNT_WORDS = r"(?:tonnes?|tons?|employees|people|persons|parcels|shipments|orders|pin[\s-]?codes|customers|days|units|vehicles|tractors|sq\.?\s?ft\.?|square\s+feet|sqft|kilomet(?:re|er)s?|km|cities|states|countries|facilities|centres|centers|hubs|stores|branches)"

# One regex to find every numeric mention with its immediate unit context.
VALUE_RE = re.compile(
    rf"(?P<cur>(?:₹|\$|€|£|\bRs\.?|\bINR|\bUSD|\bUS\$|\bI(?=\s\d))\s?)?"
    rf"(?P<num>{NUMBER})"
    rf"(?:\s?(?P<scale>{SCALE_WORDS})\b)?"
    rf"(?:\s?(?P<pct>{PCT_WORDS}))?"
    rf"(?:\s?(?P<count>{COUNT_WORDS})\b)?",
    re.IGNORECASE,
)


@dataclass
class ParsedValue:
    raw: str
    numeric: float            # as written, e.g. 8142
    normalized: float         # base units, e.g. 81_420_000_000
    unit: str                 # as written, e.g. "INR Cr"
    normalized_unit: str      # INR | USD | PERCENT | COUNT | TONNES
    kind: str                 # money | percent | count
    start: int = 0
    end: int = 0
    negative: bool = False
    count_word: str = ""      # explicit count noun after the number, e.g. "employees", "pin codes"


def _to_float(num_text: str) -> Optional[float]:
    neg = num_text.startswith("(") and num_text.endswith(")")
    cleaned = num_text.strip("()").replace(",", "")
    try:
        val = float(cleaned)
    except ValueError:
        return None
    return -abs(val) if neg else val


def prenormalize(text: str) -> str:
    """Rewrite accounting-style negatives so the value regex sees a sign: (452) -> -452, (6.3%) -> -6.3%."""
    text = re.sub(r"\((\d[\d,]*(?:\.\d+)?)\s?(%|per\s?cent)\)", r"-\1\2", text)
    text = re.sub(rf"\((\d[\d,]*(?:\.\d+)?)(\s?{SCALE_WORDS})\)", r"-\1\2", text, flags=re.IGNORECASE)
    text = re.sub(r"\((\d[\d,]*(?:\.\d+)?)\)", r"-\1", text)
    return text


def parse_values(text: str, masked_spans: Optional[List[Tuple[int, int]]] = None) -> List[ParsedValue]:
    """Find every numeric value in a sentence with its unit context.

    `masked_spans` are character ranges (e.g. detected period mentions such as
    "2024-25" or "December 31, 2021") whose digits must not be read as values.
    """
    out: List[ParsedValue] = []
    masked = masked_spans or []
    for m in VALUE_RE.finditer(text):
        if any(not (m.end("num") <= s or m.start("num") >= e) for s, e in masked):
            continue
        num_text = m.group("num")
        val = _to_float(num_text)
        if val is None or re.fullmatch(r"[0,.()]+", num_text):
            continue
        cur = (m.group("cur") or "")
        scale = (m.group("scale") or "").lower()
        pct = m.group("pct")
        count = (m.group("count") or "").lower()

        # A bare 4-digit integer that looks like a year is not a value.
        if not cur and not scale and not pct and not count and re.fullmatch(r"(19|20)\d\d", num_text.strip("()")):
            continue
        # Footnote markers / list numbers: bare small integers with no unit are useless.
        if not cur and not scale and not pct and not count and re.fullmatch(r"\d{1,2}", num_text):
            continue

        currency = ""
        for code, pats in CURRENCY_TOKENS.items():
            if cur.strip() and any(re.match(p, cur, re.IGNORECASE) for p in pats):
                currency = code
                break

        if pct:
            kind, norm_unit = "percent", "PERCENT"
            mult = 0.01 if pct.lower() == "bps" else 1.0
            unit = "%"
        elif currency:
            kind, norm_unit = "money", currency
            mult = SCALE.get(scale, 1.0)
            unit = f"{currency} {scale.title()}".strip() if scale else currency
        elif count in ("tonnes", "tonne", "tons", "ton"):
            kind, norm_unit = "count", "TONNES"
            mult = SCALE.get(scale, 1.0)
            unit = f"{scale} tonnes".strip()
        else:
            if not scale and not count:
                # bare number with no unit at all: only useful when large (headcounts etc.)
                if abs(val) < 100:
                    continue
            kind, norm_unit = "count", "COUNT"
            mult = SCALE.get(scale, 1.0)
            unit = f"{scale} {count}".strip() or "count"

        out.append(ParsedValue(
            raw=m.group(0).strip().lstrip("("),
            numeric=val,
            normalized=round(val * mult, 6),
            unit=unit,
            normalized_unit=norm_unit,
            kind=kind,
            start=m.start(),
            end=m.end(),
            negative=val < 0,
            count_word=re.sub(r"[\s-]+", " ", count),
        ))
    return out


def normalize_value_and_unit(raw_val: str, raw_unit: str = "") -> Tuple[Optional[float], Optional[float], str, str]:
    """Backwards-compatible helper used by tests: parse a single value string."""
    text = prenormalize(f"{raw_val} {raw_unit}".strip())
    vals = parse_values(text)
    if not vals:
        # fall back to any number at all
        m = re.search(NUMBER, text)
        if not m:
            return None, None, raw_unit, "TEXT"
        v = _to_float(m.group(0))
        return v, v, raw_unit or "count", "COUNT"
    v = vals[0]
    return v.numeric, v.normalized, v.unit, v.normalized_unit


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
MONTH_RE = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_RE = rf"(?P<month>{MONTH_RE})\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<year>(?:19|20)\d\d)"

# Ordered list of (regex, handler). Handlers return FactPeriod or None.
_PERIOD_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(rf"\b(?:nine|six|three)\s+months?\s+(?:period\s+)?ended\s+{DATE_RE}", re.I), "ytd"),
    (re.compile(rf"\b(?:financial\s+|fiscal\s+)?year\s+end(?:ed|ing)\s+(?:on\s+)?{DATE_RE}", re.I), "fy_end_date"),
    (re.compile(r"\b(?P<h>first|second)\s+half\s+of\s+(?:FY\s?|fiscal\s+)?(?P<y>\d{2,4})(?:-\d{2})?\b", re.I), "half_words"),
    (re.compile(r"\bH(?P<h>[12])\s?(?:FY\s?)?(?P<y>\d{2,4})\b", re.I), "half"),
    (re.compile(r"\bQ(?P<q>[1-4])\s*(?:of\s+)?(?:the\s+)?(?:FY\s?|fiscal\s+(?:year\s+)?)?(?P<y>\d{2,4})\b", re.I), "quarter"),
    (re.compile(r"\b(?P<y>20\d\d)\s?Q(?P<q>[1-4])\b", re.I), "quarter_yq"),
    (re.compile(r"\b(?P<q>first|second|third|fourth)\s+quarter\s+of\s+(?:FY\s?|fiscal\s+)?(?P<y>\d{2,4})(?:[-/]\d{2})?\b", re.I), "quarter_words"),
    (re.compile(r"\bFY\s?(?P<y1>\d{4})\s?[-/]\s?(?P<y2>\d{2,4})\b", re.I), "fy_range"),
    (re.compile(r"\bFY\s?(?P<y>\d{2,4})\b", re.I), "fy"),
    (re.compile(r"\bfiscal\s+(?:year\s+)?(?P<y>20\d\d)\b", re.I), "fy"),
    (re.compile(r"\b(?P<y1>20\d\d)\s?[-–]\s?(?P<y2>\d{2})\b(?:\s?\((?:RE|BE|PE|FRE)\))?", re.I), "fy_range"),
    (re.compile(rf"\b(?:as\s+(?:of|on|at)\s+)?{DATE_RE}", re.I), "date"),
    (re.compile(rf"\b(?:in|during|for|as\s+(?:of|on|at)|end(?:-|\s+of\s+))\s*(?P<month>{MONTH_RE})\s+(?P<year>(?:19|20)\d\d)\b", re.I), "month"),
    (re.compile(r"\b(?:in|during|for|calendar\s+year|CY)\s+(?P<y>20\d\d)\b", re.I), "cy"),
]


def _fy(year: int, raw: str) -> FactPeriod:
    return FactPeriod(raw=raw, period_type=PeriodType.FISCAL_YEAR, canonical=f"FY{year}",
                      start_date=f"{year - 1}-04-01", end_date=f"{year}-03-31")


def _yy(y: str) -> int:
    y = y.strip()
    if len(y) == 2:
        return 2000 + int(y)
    if len(y) == 4:
        return int(y)
    return int(y)


def _date_from(m: re.Match) -> Optional[str]:
    try:
        mo = MONTHS[m.group("month").lower()]
        return f"{int(m.group('year')):04d}-{mo:02d}-{int(m.group('day')):02d}"
    except (KeyError, ValueError):
        return None


def find_periods(text: str) -> List[Tuple[int, int, FactPeriod]]:
    """Return every period mention in the text as (start, end, FactPeriod)."""
    found: List[Tuple[int, int, FactPeriod]] = []
    taken: List[Tuple[int, int]] = []

    def overlaps(a: int, b: int) -> bool:
        return any(not (b <= s or a >= e) for s, e in taken)

    for pat, kind in _PERIOD_PATTERNS:
        for m in pat.finditer(text):
            if overlaps(m.start(), m.end()):
                continue
            raw = m.group(0).strip()
            p: Optional[FactPeriod] = None
            try:
                if kind == "fy":
                    p = _fy(_yy(m.group("y")), raw)
                elif kind == "fy_range":
                    y2 = m.group("y2")
                    y1 = int(m.group("y1"))
                    end = _yy(y2) if len(y2) == 4 else (y1 // 100) * 100 + int(y2)
                    if end < y1:
                        end += 100
                    if end - y1 != 1:
                        continue  # "2011-12 prices" style ranges are fine, but "2015-2030" is not a fiscal year
                    p = _fy(end, raw)
                elif kind == "fy_end_date":
                    iso = _date_from(m)
                    if iso:
                        y, mo = int(iso[:4]), int(iso[5:7])
                        p = _fy(y if mo == 3 else y, raw)
                        p.start_date, p.end_date = None, iso
                elif kind == "ytd":
                    iso = _date_from(m)
                    if iso:
                        n = raw.split()[0].lower()
                        months = {"nine": 9, "six": 6, "three": 3}[n]
                        p = FactPeriod(raw=raw, period_type=PeriodType.DATE_RANGE,
                                       canonical=f"{months}M-ended-{iso}", end_date=iso)
                elif kind in ("quarter", "quarter_yq", "quarter_words"):
                    q = m.group("q")
                    q = {"first": "1", "second": "2", "third": "3", "fourth": "4"}.get(q.lower(), q)
                    p = FactPeriod(raw=raw, period_type=PeriodType.QUARTER, canonical=f"FY{_yy(m.group('y'))}-Q{q}")
                elif kind in ("half", "half_words"):
                    h = m.group("h")
                    h = {"first": "1", "second": "2"}.get(h.lower(), h)
                    p = FactPeriod(raw=raw, period_type=PeriodType.DATE_RANGE, canonical=f"FY{_yy(m.group('y'))}-H{h}")
                elif kind == "date":
                    iso = _date_from(m)
                    if iso:
                        p = FactPeriod(raw=raw, period_type=PeriodType.POINT_IN_TIME, canonical=iso,
                                       start_date=iso, end_date=iso)
                elif kind == "month":
                    mo = MONTHS[m.group("month").lower()]
                    iso = f"{int(m.group('year')):04d}-{mo:02d}"
                    p = FactPeriod(raw=raw, period_type=PeriodType.POINT_IN_TIME, canonical=iso)
                elif kind == "cy":
                    y = int(m.group("y"))
                    p = FactPeriod(raw=raw, period_type=PeriodType.FISCAL_YEAR, canonical=f"CY{y}",
                                   start_date=f"{y}-01-01", end_date=f"{y}-12-31")
            except (ValueError, KeyError):
                p = None
            if p:
                found.append((m.start(), m.end(), p))
                taken.append((m.start(), m.end()))
    found.sort(key=lambda t: t[0])
    return found


def normalize_period(period_raw: str, is_flow_metric: bool = False) -> FactPeriod:
    """Parse a single period string (used by tests and the timeline)."""
    if not period_raw:
        return FactPeriod(raw="", period_type=PeriodType.UNKNOWN, canonical="unknown")
    hits = find_periods(period_raw)
    if hits:
        p = hits[0][2]
        p.raw = period_raw.strip()
        # "March 31, 2024" on a flow metric means the fiscal year ending then
        if is_flow_metric and p.period_type == PeriodType.POINT_IN_TIME and p.canonical.endswith("-03-31"):
            return _fy(int(p.canonical[:4]), period_raw.strip())
        return p
    return FactPeriod(raw=period_raw, period_type=PeriodType.UNKNOWN, canonical="unknown")


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

_CORP_SUFFIX = r"(?:private|pvt\.?|limited|ltd\.?|inc\.?|incorporated|corporation|corp\.?|llc|llp|plc|co\.?|company|the)"


def normalize_entity(entity_name: str) -> str:
    if not entity_name:
        return "unknown_entity"
    name = entity_name.lower().strip()
    name = re.sub(rf"\b{_CORP_SUFFIX}\b", " ", name)
    name = re.sub(r"[^\w\s]", " ", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name or "unknown_entity"


# ---------------------------------------------------------------------------
# Metric taxonomy
# ---------------------------------------------------------------------------

@dataclass
class MetricSpec:
    canonical: str
    label: str
    category: MetricCategory
    keywords: List[str]                 # lower-case phrases; longest matched wins
    value_kind: str                     # money | percent | count
    is_flow: bool = True                # flow (period) vs stock (point in time)
    subject: str = "company"            # company | country
    requires: List[str] = field(default_factory=list)   # extra phrases that must also be present
    sign_negative_if: List[str] = field(default_factory=list)  # e.g. ["loss"]
    non_gaap_if: List[str] = field(default_factory=list)       # e.g. ["adjusted", "adj."]
    count_unit: Optional[str] = None    # expected normalized unit for counts (COUNT/TONNES)
    count_words: List[str] = field(default_factory=list)  # nouns that may follow the number, e.g. ["employees"]
    weak_keywords: List[str] = field(default_factory=list)  # keywords that need a stock-style phrasing to count
    exclude_near: List[str] = field(default_factory=list)  # words that, near the value, mean it is a component/other series


METRICS: List[MetricSpec] = [
    MetricSpec("financial.revenue", "Revenue", MetricCategory.FINANCIAL_FLOW,
               ["revenue from operations", "revenue from services", "revenue from contracts with customers",
                "total revenue", "revenues", "revenue", "turnover", "topline"],
               "money", exclude_near=["sector", "industry", "market"]),
    MetricSpec("financial.total_income", "Total income", MetricCategory.FINANCIAL_FLOW,
               ["total income"], "money"),
    MetricSpec("financial.ebitda", "EBITDA", MetricCategory.FINANCIAL_FLOW,
               ["adjusted ebitda", "adj. ebitda", "adj ebitda", "ebitda", "operating profit", "operating income"],
               "money", non_gaap_if=["adjusted", "adj."]),
    MetricSpec("financial.ebitda_margin", "EBITDA margin", MetricCategory.FINANCIAL_FLOW,
               ["adjusted ebitda margin", "adj. ebitda margin", "ebitda margin"],
               "percent", non_gaap_if=["adjusted", "adj."]),
    MetricSpec("financial.net_income", "Net profit / loss", MetricCategory.FINANCIAL_FLOW,
               ["profit after tax", "net profit", "net loss", "loss after tax", "loss for the year",
                "loss for the period", "profit for the year", "profit for the period", "net income", "pat"],
               "money", sign_negative_if=["loss"]),
    MetricSpec("financial.capex", "Capital expenditure", MetricCategory.FINANCIAL_FLOW,
               ["capital expenditure", "capex"], "money"),
    MetricSpec("financial.net_worth", "Net worth", MetricCategory.FINANCIAL_STOCK,
               ["net worth"], "money", is_flow=False),
    MetricSpec("financial.borrowings", "Borrowings / debt", MetricCategory.FINANCIAL_STOCK,
               ["total borrowings", "gross debt", "net debt", "borrowings"], "money", is_flow=False),
    MetricSpec("financial.cash", "Cash and equivalents", MetricCategory.FINANCIAL_STOCK,
               ["cash and cash equivalents", "cash and bank balances", "cash balance"], "money", is_flow=False),

    MetricSpec("ops.parcel_volume", "Express parcel shipments", MetricCategory.OPERATIONAL,
               ["express parcel shipments", "express parcels shipped", "express parcel volume", "express parcel orders",
                "parcels shipped", "parcel volume", "shipments", "parcels"], "count", count_unit="COUNT",
               count_words=["parcels", "shipments", "orders"], weak_keywords=["shipments", "parcels"]),
    MetricSpec("ops.ptl_tonnage", "PTL freight tonnage", MetricCategory.OPERATIONAL,
               ["ptl freight tonnage", "ptl freight delivered", "part truckload tonnage", "part-truckload tonnage",
                "ptl volume", "tonnes of ptl freight", "ptl freight", "freight tonnage"], "count", count_unit="TONNES"),
    MetricSpec("ops.pin_codes", "Pin codes served", MetricCategory.OPERATIONAL,
               ["pin codes covered", "pin codes", "pin-codes", "pincodes"], "count", is_flow=False, count_unit="COUNT",
               count_words=["pin codes", "pin-codes", "pincodes"],
               exclude_near=["of the", "in india", "out of", "as per", "nationwide", "country"]),
    MetricSpec("ops.headcount", "Headcount", MetricCategory.OPERATIONAL,
               ["permanent employees", "total headcount", "headcount", "team size", "employees on roll", "employees"],
               "count", is_flow=False, count_unit="COUNT", count_words=["employees", "people", "persons"],
               weak_keywords=["employees"]),
    MetricSpec("ops.active_customers", "Active customers", MetricCategory.OPERATIONAL,
               ["active customers"], "count", is_flow=False, count_unit="COUNT", count_words=["customers"],
               exclude_near=["segment", "vertical", "category", "business line", "of which", "top ", "largest", "new "]),
    MetricSpec("ops.working_capital_days", "Net working capital days", MetricCategory.OPERATIONAL,
               ["net working capital days", "nwc days", "net working capital cycle", "working capital days"],
               "count", is_flow=False, count_unit="COUNT", count_words=["days"]),

    MetricSpec("macro.nominal_gdp_growth", "Nominal GDP growth", MetricCategory.GENERAL,
               ["nominal gdp growth", "nominal gdp grew", "nominal gdp"], "percent", subject="country"),
    MetricSpec("macro.gdp_growth", "Real GDP growth", MetricCategory.GENERAL,
               ["real gdp growth", "real gdp grew", "real gdp expanded", "gdp growth", "real gross domestic product",
                "gdp at constant prices", "gdp at constant", "gross domestic product (gdp) growth",
                "gdp grew", "gdp is estimated to grow", "gdp growth rate", "economy grew", "economy is projected to grow",
                "growth is projected", "growth is estimated"],
               "percent", subject="country",
               exclude_near=["nominal", "gva", "sector", "industrial", "agriculture", "services", "manufacturing",
                             "global", "world", "euro area", "advanced economies", "capital formation", "consumption"]),
    MetricSpec("macro.inflation", "Headline CPI inflation", MetricCategory.GENERAL,
               ["headline inflation", "cpi inflation", "retail inflation", "consumer price inflation", "headline cpi"],
               "percent", subject="country",
               exclude_near=["food", "core", "fuel", "contribution", "weight", "share", "excluding", "vegetables",
                             "global", "wholesale", "wpi", "target", "projection", "band", "tolerance", "threshold"]),
    MetricSpec("macro.cad_gdp", "Current account deficit (% of GDP)", MetricCategory.GENERAL,
               ["current account deficit", "cad"], "percent", subject="country", requires=["gdp"]),
    MetricSpec("macro.fiscal_deficit_gdp", "Fiscal deficit (% of GDP)", MetricCategory.GENERAL,
               ["gross fiscal deficit", "fiscal deficit"], "percent", subject="country", requires=["gdp"]),
    MetricSpec("macro.policy_rate", "Policy repo rate", MetricCategory.GENERAL,
               ["policy repo rate", "repo rate"], "percent", subject="country", is_flow=False),
    MetricSpec("macro.forex_reserves", "Foreign exchange reserves", MetricCategory.GENERAL,
               ["foreign exchange reserves", "forex reserves", "fx reserves"], "money", subject="country", is_flow=False),
]

METRIC_BY_CANONICAL: Dict[str, MetricSpec] = {m.canonical: m for m in METRICS}

# Flat alias index sorted longest-first so "adjusted ebitda margin" beats "ebitda".
_ALIASES: List[Tuple[str, MetricSpec]] = sorted(
    ((kw, spec) for spec in METRICS for kw in spec.keywords), key=lambda t: -len(t[0])
)


def find_metric_mentions(sentence_lower: str) -> List[Tuple[int, int, MetricSpec, str]]:
    """Return (start, end, spec, matched_keyword) for every taxonomy keyword in the sentence."""
    hits: List[Tuple[int, int, MetricSpec, str]] = []
    taken: List[Tuple[int, int]] = []
    for kw, spec in _ALIASES:
        for m in re.finditer(rf"(?<![a-z]){re.escape(kw)}(?![a-z])", sentence_lower):
            if any(not (m.end() <= s or m.start() >= e) for s, e in taken):
                continue
            if spec.requires and not all(r in sentence_lower for r in spec.requires):
                continue
            hits.append((m.start(), m.end(), spec, kw))
            taken.append((m.start(), m.end()))
    hits.sort(key=lambda h: h[0])
    return hits


def normalize_metric(metric_name: str) -> Tuple[str, MetricCategory]:
    """Map a free-text metric label to a canonical key (taxonomy hit or generated slug)."""
    if not metric_name:
        return "general.unknown", MetricCategory.GENERAL
    cleaned = re.sub(r"[^\w\s.]", "", metric_name.lower()).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    hits = find_metric_mentions(cleaned)
    if hits:
        spec = hits[0][2]
        return spec.canonical, spec.category
    slug = re.sub(r"[^\w]+", "_", cleaned).strip("_")[:60]
    return f"metric.{slug or 'unknown'}", MetricCategory.GENERAL


# ---------------------------------------------------------------------------
# Context qualifiers
# ---------------------------------------------------------------------------

# A forward-looking claim is a projection whoever makes it, so the verb forms count too:
# "X is expected to be 4 per cent" and "the RBI expects X to be 4 per cent" are the same claim,
# and treating only one of them as an estimate makes two forecasts look like a revision.
ESTIMATE_WORDS = ["estimated", "estimate", "estimates", "projected", "projection", "projections", "forecast",
                  "forecasts", "expected to", "is expected", "expects", "expect to", "anticipates",
                  "is projected", "are projected", "likely to", "advance estimate", "provisional",
                  "(re)", "(be)", "(pe)", "revised estimate", "budget estimate", "outlook", "target"]


def detect_qualifiers(sentence_lower: str) -> List[str]:
    q: List[str] = []
    if any(w in sentence_lower for w in ESTIMATE_WORDS):
        q.append("estimate")
    if "pro forma" in sentence_lower or "proforma" in sentence_lower:
        q.append("pro_forma")
    if "excluding" in sentence_lower or "excl." in sentence_lower or "ex-" in sentence_lower:
        q.append("exclusions_apply")
    if "yoy" in sentence_lower or "year-on-year" in sentence_lower or "y-o-y" in sentence_lower:
        q.append("yoy_growth_present")
    if re.search(rf"\(?\b{MONTH_RE}\s*(?:-|–|to)\s*{MONTH_RE}\b\)?", sentence_lower) or re.search(r"\bh[12]\b|first half|second half|nine months|six months|\bq[1-4]\b", sentence_lower):
        q.append("partial_period")
    if re.search(r"\bper\s+(day|hour|month|annum|year)\b|\bcapacity\b|\bthroughput\b|\bdaily\b", sentence_lower):
        q.append("rate_or_capacity")
    if re.search(r"\bsince (inception|incorporation)\b|\bcumulative\b|\btill date\b", sentence_lower):
        q.append("cumulative")
    return q


def detect_scope(sentence_lower: str) -> str:
    if "standalone" in sentence_lower:
        return "standalone"
    if "consolidated" in sentence_lower or "group" in sentence_lower:
        return "consolidated"
    if "segment" in sentence_lower or "business line" in sentence_lower:
        return "segment"
    return "unspecified"


class FactNormalizer:
    """Thin façade kept for backwards compatibility with earlier imports/tests."""
    normalize_entity = staticmethod(normalize_entity)
    normalize_metric = staticmethod(normalize_metric)
    normalize_period = staticmethod(normalize_period)
    normalize_value_and_unit = staticmethod(normalize_value_and_unit)
