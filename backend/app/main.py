"""
Fact Knowledge Layer API.

Upload PDFs -> facts with evidence -> pairwise relationships with a reasoning
trace. State lives in memory and is snapshotted to backend/data/store.json so
a restart does not lose the knowledge layer.
"""
import json
import os
import shutil
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .models.document import Document, DocumentMetadata
from .models.fact import Fact
from .models.relationship import FactRelationship, RelationshipType
from .services.canonicalizer import assign_families
from .services.extractor import MATCH_THRESHOLD, FactExtractor
from .services.matcher import CrossDocumentMatcher
from .services.normalizer import METRICS
from .services.parser import PDFParser

app = FastAPI(
    title="Fact Knowledge Layer",
    description="Extracts grounded facts from PDFs and explains how they corroborate, contradict, or reconcile.",
    version="2.0.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

DOCUMENTS: Dict[str, Document] = {}
FACTS: Dict[str, Fact] = {}
RELATIONSHIPS: Dict[str, FactRelationship] = {}

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(ROOT_DIR, "backend", "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
STORE_PATH = os.path.join(DATA_DIR, "store.json")
STARTER_DIR = os.path.join(ROOT_DIR, "starter-datasets")
os.makedirs(UPLOAD_DIR, exist_ok=True)


# --------------------------------------------------------------------------- persistence
def save_store() -> None:
    payload = {
        "documents": [d.model_dump() for d in DOCUMENTS.values()],
        "facts": [f.model_dump() for f in FACTS.values()],
        "relationships": [r.model_dump() for r in RELATIONSHIPS.values()],
    }
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, STORE_PATH)


def load_store() -> None:
    if not os.path.exists(STORE_PATH):
        return
    try:
        with open(STORE_PATH, encoding="utf-8") as fh:
            payload = json.load(fh)
        for d in payload.get("documents", []):
            doc = Document(**d)
            DOCUMENTS[doc.metadata.id] = doc
        for f in payload.get("facts", []):
            fact = Fact(**f)
            FACTS[fact.id] = fact
        for r in payload.get("relationships", []):
            rel = FactRelationship(**r)
            RELATIONSHIPS[rel.id] = rel
    except Exception:  # a stale snapshot from an older schema should not stop the server
        DOCUMENTS.clear(); FACTS.clear(); RELATIONSHIPS.clear()


load_store()


# --------------------------------------------------------------------------- helpers
def ingest_file(path: str, original_name: str) -> List[Fact]:
    # re-uploading a file with the same name replaces the earlier copy instead of doubling its facts
    for old_id in [d.metadata.id for d in DOCUMENTS.values() if d.metadata.original_name == original_name]:
        del DOCUMENTS[old_id]
        for fid in [fid for fid, f in FACTS.items() if f.document_id == old_id]:
            del FACTS[fid]
        for rid in [rid for rid, r in RELATIONSHIPS.items() if r.fact_a_id not in FACTS or r.fact_b_id not in FACTS]:
            del RELATIONSHIPS[rid]
    doc = PDFParser.parse_pdf(path, original_name)
    facts = FactExtractor.extract_from_document(doc)
    doc.metadata.extracted_facts_count = len(facts)
    doc.metadata.status = "extracted"
    DOCUMENTS[doc.metadata.id] = doc
    for f in facts:
        FACTS[f.id] = f
    return facts


def rebuild_relationships() -> None:
    RELATIONSHIPS.clear()
    facts = list(FACTS.values())
    # families are assigned over the whole layer, because a family only earns its
    # keep when it brings together labels from different documents
    assign_families(facts)
    for rel in CrossDocumentMatcher.match_facts(facts):
        RELATIONSHIPS[rel.id] = rel


def enrich(rel: FactRelationship) -> Optional[Dict[str, Any]]:
    a, b = FACTS.get(rel.fact_a_id), FACTS.get(rel.fact_b_id)
    if not a or not b:
        return None
    return {"relationship": rel, "fact_a": a, "fact_b": b, "cross_document": a.document_id != b.document_id}


def clear_all() -> None:
    DOCUMENTS.clear(); FACTS.clear(); RELATIONSHIPS.clear()


# --------------------------------------------------------------------------- routes
@app.get("/")
def root():
    return {"service": "Fact Knowledge Layer", "documents": len(DOCUMENTS), "facts": len(FACTS), "relationships": len(RELATIONSHIPS)}


@app.get("/api/status")
def status():
    """What mode the server is actually running in - visible without reading logs."""
    s = settings()
    return {
        "llm_enabled": s.llm_enabled(),
        "mode": s.mode,
        "model_fast": s.model_fast if s.llm_enabled() else None,
        "model_strong": s.model_strong if s.llm_enabled() else None,
        "documents": len(DOCUMENTS),
        "facts": len(FACTS),
        "relationships": len(RELATIONSHIPS),
    }


@app.post("/api/documents/upload", response_model=List[DocumentMetadata])
async def upload_documents(files: List[UploadFile] = File(...), incremental: bool = Query(True)):
    """Upload one or more PDFs. incremental=true (default) compares only the new facts against the existing layer."""
    new_facts: List[Fact] = []
    existing = list(FACTS.values())
    metas: List[DocumentMetadata] = []
    for file in files:
        if not (file.filename or "").lower().endswith(".pdf"):
            continue
        path = os.path.join(UPLOAD_DIR, os.path.basename(file.filename))
        with open(path, "wb") as out:
            shutil.copyfileobj(file.file, out)
        try:
            facts = ingest_file(path, file.filename)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Could not parse {file.filename}: {exc}")
        new_facts.extend(facts)
        metas.append(next(d.metadata for d in DOCUMENTS.values() if d.metadata.original_name == file.filename))
    if not metas:
        raise HTTPException(status_code=400, detail="No PDF files were provided.")
    if incremental and existing:
        # families are a global grouping - existing facts already carry one from the
        # last rebuild, so new facts must be regrouped together with them before
        # matching, or a family and its exact-key twin never meet in match_incremental
        assign_families(existing + new_facts)
        for rel in CrossDocumentMatcher.match_incremental(new_facts, existing):
            RELATIONSHIPS[rel.id] = rel
    else:
        rebuild_relationships()
    save_store()
    return metas


@app.post("/api/documents/upload-incremental", response_model=List[DocumentMetadata])
async def upload_documents_incremental(files: List[UploadFile] = File(...)):
    return await upload_documents(files, incremental=True)


@app.post("/api/load-preset")
def load_preset(name: str = Query("delhivery"), replace: bool = Query(True)):
    """Load a sample dataset folder from starter-datasets/ (any sub-folder name works)."""
    folder = {"india-macro": "india-macroeconomy"}.get(name, name)
    preset_dir = os.path.join(STARTER_DIR, folder)
    if not os.path.isdir(preset_dir):
        raise HTTPException(status_code=404, detail=f"No starter dataset folder named '{name}'.")
    if replace:
        clear_all()
    loaded = []
    for pdf in sorted(f for f in os.listdir(preset_dir) if f.lower().endswith(".pdf")):
        src = os.path.join(preset_dir, pdf)
        dst = os.path.join(UPLOAD_DIR, pdf)
        if os.path.abspath(src) != os.path.abspath(dst):
            shutil.copy2(src, dst)
        ingest_file(dst, pdf)
        loaded.append(pdf)
    rebuild_relationships()
    save_store()
    return {"preset": name, "documents": loaded, "total_facts": len(FACTS), "total_relationships": len(RELATIONSHIPS)}


@app.get("/api/presets")
def list_presets():
    if not os.path.isdir(STARTER_DIR):
        return []
    out = []
    for name in sorted(os.listdir(STARTER_DIR)):
        d = os.path.join(STARTER_DIR, name)
        if os.path.isdir(d):
            pdfs = [f for f in os.listdir(d) if f.lower().endswith(".pdf")]
            if pdfs:
                out.append({"name": name, "files": sorted(pdfs)})
    return out


@app.get("/api/documents", response_model=List[DocumentMetadata])
def list_documents():
    return [d.metadata for d in DOCUMENTS.values()]


@app.get("/api/documents/{doc_id}/pages/{page_number}")
def get_page(doc_id: str, page_number: int):
    doc = DOCUMENTS.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if page_number < 1 or page_number > len(doc.pages):
        raise HTTPException(status_code=404, detail="Page not found")
    page = doc.pages[page_number - 1]
    return {"document_id": doc_id, "document_name": doc.metadata.original_name, "page_number": page_number,
            "page_count": len(doc.pages), "text": page.text, "tiles": page.tiles}


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str):
    if doc_id not in DOCUMENTS:
        raise HTTPException(status_code=404, detail="Document not found")
    del DOCUMENTS[doc_id]
    for fid in [fid for fid, f in FACTS.items() if f.document_id == doc_id]:
        del FACTS[fid]
    rebuild_relationships()
    save_store()
    return {"status": "deleted", "documents": len(DOCUMENTS), "facts": len(FACTS)}


@app.get("/api/facts", response_model=List[Fact])
def list_facts(doc_id: Optional[str] = None, entity: Optional[str] = None, metric: Optional[str] = None,
               min_confidence: float = Query(0.0), q: Optional[str] = None):
    out = list(FACTS.values())
    if doc_id:
        out = [f for f in out if f.document_id == doc_id]
    if entity:
        out = [f for f in out if entity.lower() in f.entity_canonical.lower()]
    if metric:
        out = [f for f in out if metric.lower() in f.metric_canonical.lower()]
    if min_confidence:
        out = [f for f in out if f.confidence >= min_confidence]
    if q:
        ql = q.lower()
        out = [f for f in out if ql in f.source_evidence.exact_quote.lower() or ql in f.metric.lower() or ql in f.raw_value.lower()]
    return out


@app.get("/api/facts/{fact_id}", response_model=Fact)
def get_fact(fact_id: str):
    if fact_id not in FACTS:
        raise HTTPException(status_code=404, detail="Fact not found")
    return FACTS[fact_id]


@app.get("/api/relationships")
def list_relationships(rel_type: Optional[str] = None, scope: str = Query("all", pattern="^(all|cross|intra)$"),
                       metric: Optional[str] = None, entity: Optional[str] = None):
    out = []
    for rel in RELATIONSHIPS.values():
        if rel_type and rel.relationship_type.value != rel_type:
            continue
        if metric and metric.lower() not in rel.metric_canonical.lower():
            continue
        if entity and entity.lower() not in rel.entity_canonical.lower():
            continue
        e = enrich(rel)
        if not e:
            continue
        if scope == "cross" and not e["cross_document"]:
            continue
        if scope == "intra" and e["cross_document"]:
            continue
        out.append(e)
    out.sort(key=lambda e: (-int(e["cross_document"]), -e["relationship"].confidence))
    return out


@app.get("/api/cases")
def four_cases():
    """The four cases the assignment asks for, chosen from the live data (nothing hard-coded)."""
    enriched = [e for e in (enrich(r) for r in RELATIONSHIPS.values()) if e]

    def pick(kind: RelationshipType, factor: Optional[str] = None, prefer_cross: bool = True, limit: int = 3):
        rows = [e for e in enriched if e["relationship"].relationship_type == kind
                and (factor is None or e["relationship"].reasoning_trace.primary_divergence_factor == factor)]
        rows.sort(key=lambda e: (
            -int(e["cross_document"]) if prefer_cross else 0,
            -min(e["fact_a"].confidence, e["fact_b"].confidence),
            # "expressed differently" is more interesting than identical strings
            0 if e["fact_a"].raw_value.lower() != e["fact_b"].raw_value.lower() else 1,
        ))
        return rows[:limit]

    explained = []
    # IDENTIFIER_REVISED first: "same identifier, one character apart, explained by a corporate
    # event" is the most concrete and immediately legible of these cases - lead with it rather
    # than the more generic basis/timing factors.
    for factor in ("IDENTIFIER_REVISED", "SCOPE_OR_BASIS", "STATUS_CHANGED_OVER_TIME", "DATA_VINTAGE",
                   "PARTIAL_PERIOD", "DIFFERENT_PERIODS"):
        explained.extend(pick(RelationshipType.CONTEXTUALLY_EXPLAINED, factor, limit=1))

    low = sorted((f for f in FACTS.values() if f.confidence < MATCH_THRESHOLD), key=lambda f: f.confidence)
    reasons = Counter()
    for f in low:
        # keep the whole first sentence: notes contain "(...)" ellipses that a bare "." split would cut
        reasons[(f.notes or "Low confidence").split(". ")[0].rstrip(".")] += 1

    return {
        "corroborated": pick(RelationshipType.CORROBORATED),
        "contradiction": pick(RelationshipType.CONTRADICTION),
        "contextually_explained": explained,
        "needs_review": pick(RelationshipType.INSUFFICIENT_EVIDENCE, limit=3),
        "extraction_failures": {
            "count": len(low),
            "reasons": [{"reason": k, "count": v} for k, v in reasons.most_common(8)],
            "examples": low[:8],
        },
    }


@app.get("/api/insights")
def insights():
    """What a reviewer should actually look at, computed from the loaded documents.

    Four things: a ranked review queue, which document pairs agree or disagree,
    which metrics are confirmed by more than one source, and where extraction is
    weakest. Ranking is deterministic and its reason is returned in words.
    """
    enriched = [e for e in (enrich(r) for r in RELATIONSHIPS.values()) if e]
    facts = list(FACTS.values())
    docs = list(DOCUMENTS.values())

    # ---------------------------------------------------------------- review queue
    BASE = {
        (RelationshipType.CONTRADICTION, True): (100, "Two documents disagree"),
        (RelationshipType.CONTRADICTION, False): (55, "One document disagrees with itself"),
        (RelationshipType.INSUFFICIENT_EVIDENCE, True): (45, "Two documents partly overlap"),
        (RelationshipType.INSUFFICIENT_EVIDENCE, False): (35, "Same label used twice in one document"),
    }
    queue = []
    for e in enriched:
        rel, a, b = e["relationship"], e["fact_a"], e["fact_b"]
        key = (rel.relationship_type, e["cross_document"])
        if key not in BASE:
            continue
        base, headline = BASE[key]
        delta = rel.delta_percentage
        # a large disagreement matters more than a rounding-sized one
        spread = 0.0
        if delta is not None:
            spread = min(25.0, delta / 4.0)
        elif a.normalized_unit == "TEXT":
            spread = 12.0
        score = base * rel.confidence + spread
        period = a.period.raw or a.period.canonical
        queue.append({
            "relationship_id": rel.id,
            "severity": round(score, 1),
            "headline": headline,
            "what": f"{a.metric} for {period}" if period not in ("", "unknown") else a.metric,
            "entity": a.entity,
            "verdict": rel.relationship_type.value,
            "cross_document": e["cross_document"],
            "values": [a.raw_value, b.raw_value],
            "documents": sorted({a.document_name, b.document_name}),
            "delta_percentage": delta,
            "summary": rel.reasoning_trace.summary,
            "fact_a": a, "fact_b": b, "relationship": rel,
        })
    queue.sort(key=lambda q: -q["severity"])

    # Several facts about the same metric and period in the same pair of documents produce many
    # near-identical rows. Keep the sharpest one and say how many others sit behind it.
    grouped: List[Dict[str, Any]] = []
    seen_group: Dict[tuple, Dict[str, Any]] = {}
    for q in queue:
        a = q["fact_a"]
        key = (q["verdict"], a.metric_canonical, a.period.canonical, a.entity_canonical, tuple(q["documents"]))
        if key in seen_group:
            seen_group[key]["also"] += 1
            continue
        q["also"] = 0
        seen_group[key] = q
        grouped.append(q)
    queue = grouped

    # ---------------------------------------------------------------- document pairs
    order = [d.metadata for d in docs]
    index = {m.id: i for i, m in enumerate(order)}
    cells: Dict[tuple, Counter] = defaultdict(Counter)
    for e in enriched:
        i, j = index.get(e["fact_a"].document_id), index.get(e["fact_b"].document_id)
        if i is None or j is None:
            continue
        cells[(min(i, j), max(i, j))][e["relationship"].relationship_type.value] += 1
    matrix = {
        "documents": [{"id": m.id, "name": m.original_name, "entity": m.primary_entity, "kind": m.document_kind,
                       "period": m.document_period or m.document_date} for m in order],
        "cells": [{"a": i, "b": j, "total": sum(c.values()),
                   "corroborated": c.get("CORROBORATED", 0), "contradiction": c.get("CONTRADICTION", 0),
                   "explained": c.get("CONTEXTUALLY_EXPLAINED", 0), "review": c.get("INSUFFICIENT_EVIDENCE", 0)}
                  for (i, j), c in sorted(cells.items())],
    }

    # ---------------------------------------------------------------- metric coverage
    per_metric: Dict[str, Dict[str, Any]] = {}
    for f in facts:
        if f.confidence < MATCH_THRESHOLD:
            continue
        row = per_metric.setdefault(f.metric_canonical, {"metric": f.metric_canonical, "label": f.metric,
                                                          "facts": 0, "documents": set(), "corroborated": 0,
                                                          "conflicting": 0})
        row["facts"] += 1
        row["documents"].add(f.document_id)
    for e in enriched:
        row = per_metric.get(e["relationship"].metric_canonical)
        if not row or not e["cross_document"]:
            continue
        t = e["relationship"].relationship_type
        if t == RelationshipType.CORROBORATED:
            row["corroborated"] += 1
        elif t == RelationshipType.CONTRADICTION:
            row["conflicting"] += 1
    coverage = sorted(({**r, "documents": len(r["documents"])} for r in per_metric.values()),
                      key=lambda r: (-r["documents"], -r["corroborated"], -r["facts"]))

    # ---------------------------------------------------------------- extraction health
    linked = {e["fact_a"].id for e in enriched} | {e["fact_b"].id for e in enriched}
    health = []
    for d in docs:
        mine = [f for f in facts if f.document_id == d.metadata.id]
        strong = [f for f in mine if f.confidence >= MATCH_THRESHOLD]
        health.append({
            "document_id": d.metadata.id, "name": d.metadata.original_name, "pages": d.metadata.page_count,
            "facts": len(mine), "matchable": len(strong), "low_confidence": len(mine) - len(strong),
            "linked": sum(1 for f in strong if f.id in linked),
        })

    matchable = [f for f in facts if f.confidence >= MATCH_THRESHOLD]
    unconfirmed = [f for f in matchable if f.id not in linked]
    conflicts = sum(1 for q in queue if q["verdict"] == "CONTRADICTION")
    # An overlap between two documents and a label reused inside one report are different
    # questions, and conflating them overstates how much cross-document disagreement there is.
    review_cross = sum(1 for q in queue if q["verdict"] == "INSUFFICIENT_EVIDENCE" and q["cross_document"])
    review_intra = sum(1 for q in queue if q["verdict"] == "INSUFFICIENT_EVIDENCE" and not q["cross_document"])
    # count facts, not pairs: "N facts confirmed by a second source" has to be comparable with
    # `unconfirmed`, which is a count of facts
    corroborated_facts = {fid
                          for e in enriched if e["relationship"].relationship_type == RelationshipType.CORROBORATED
                          for fid in (e["fact_a"].id, e["fact_b"].id)}
    return {
        "headline": {
            "needs_attention": len(queue),
            "conflicts": conflicts,
            "review_cross": review_cross,
            "review_intra": review_intra,
            "documents": len(docs),
            "facts": len(facts),
            "matchable": len(matchable),
            "unconfirmed": len(unconfirmed),
            "agreements": len(corroborated_facts),
            "agreement_pairs": sum(1 for e in enriched if e["relationship"].relationship_type == RelationshipType.CORROBORATED),
            "top": queue[0]["what"] if queue else None,
        },
        "queue": queue[:12],
        "matrix": matrix,
        "coverage": coverage[:12],
        "health": health,
    }


@app.get("/api/schema")
def schema():
    """The metric taxonomy plus every metric key discovered from documents that is not in it."""
    counts = Counter(f.metric_canonical for f in FACTS.values())
    known = {m.canonical for m in METRICS}
    return {
        "taxonomy": [{"canonical": m.canonical, "label": m.label, "category": m.category.value, "value_kind": m.value_kind,
                      "subject": m.subject, "facts": counts.get(m.canonical, 0)} for m in METRICS],
        "discovered": [{"canonical": k, "facts": v, "label": next((f.metric for f in FACTS.values() if f.metric_canonical == k), k)}
                       for k, v in counts.most_common() if k not in known],
        "match_threshold": MATCH_THRESHOLD,
    }


@app.get("/api/timeline")
def timeline(metric: str = Query(...), entity: Optional[str] = None):
    rows = [f for f in FACTS.values() if f.metric_canonical == metric and f.normalized_numeric_value is not None
            and f.period.canonical != "unknown" and (not entity or f.entity_canonical == entity)]
    rows.sort(key=lambda f: (f.period.end_date or f.period.canonical, f.document_name))
    return {"metric": metric, "points": [{
        "fact_id": f.id, "period": f.period.raw or f.period.canonical, "canonical_period": f.period.canonical,
        "value": f.raw_value, "normalized_numeric_value": f.normalized_numeric_value, "unit": f.normalized_unit,
        "document_name": f.document_name, "page_number": f.source_evidence.page_number, "exact_quote": f.source_evidence.exact_quote,
        "scope": f.context.scope, "accounting_standard": f.context.accounting_standard, "confidence": f.confidence,
        "entity": f.entity,
    } for f in rows]}


@app.get("/api/metrics-available")
def metrics_available():
    counts = Counter(f.metric_canonical for f in FACTS.values() if f.normalized_numeric_value is not None and f.period.canonical != "unknown")
    labels = {}
    for f in FACTS.values():
        labels.setdefault(f.metric_canonical, f.metric)
    return [{"canonical": k, "label": labels[k], "facts": v} for k, v in counts.most_common()]


@app.get("/api/evaluation-metrics")
def evaluation_metrics():
    facts = list(FACTS.values())
    total = len(facts)
    eligible = sum(1 for f in facts if f.confidence >= MATCH_THRESHOLD and (f.period.canonical != "unknown" or f.metric_canonical.startswith("attr.")))
    with_evidence = sum(1 for f in facts if f.source_evidence.exact_quote and f.source_evidence.page_number > 0)
    rels = list(RELATIONSHIPS.values())
    by_type = Counter(r.relationship_type.value for r in rels)
    cross = sum(1 for r in rels if (e := enrich(r)) and e["cross_document"])
    return {
        "total_documents": len(DOCUMENTS), "total_facts": total, "matchable_facts": eligible, "low_confidence_facts": total - eligible,
        "facts_with_evidence": with_evidence,
        "avg_confidence_pct": round(sum(f.confidence for f in facts) / total * 100, 1) if total else 0.0,
        "relationships": {"total": len(rels), "cross_document": cross,
                          "corroborations": by_type.get("CORROBORATED", 0), "contradictions": by_type.get("CONTRADICTION", 0),
                          "contextually_explained": by_type.get("CONTEXTUALLY_EXPLAINED", 0),
                          "needs_review": by_type.get("INSUFFICIENT_EVIDENCE", 0)},
        "match_threshold": MATCH_THRESHOLD,
    }


@app.delete("/api/clear")
def clear():
    clear_all()
    save_store()
    return {"status": "cleared"}
