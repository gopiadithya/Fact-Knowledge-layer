"""
Candidate pairing. Facts are bucketed by (entity, metric, unit) so the
reasoner only sees pairs that could possibly relate; the work is
O(sum over buckets of n_b^2) instead of O(N^2) over the whole layer.
Low-confidence facts never enter a bucket.
"""
from collections import defaultdict
from typing import Dict, List, Tuple

from ..models.fact import Fact
from ..models.relationship import FactRelationship
from .extractor import MATCH_THRESHOLD
from .reasoner import DeterministicReasoningEngine


def _eligible(f: Fact) -> bool:
    if f.confidence < MATCH_THRESHOLD:
        return False
    return f.period.canonical != "unknown" or f.metric_canonical.startswith("attr.")


def _key(f: Fact) -> Tuple[str, str, str]:
    # Facts are grouped by predicate family, so two documents naming one quantity
    # differently still meet. Whether they are the SAME claim is the reasoner's call.
    return (f.entity_canonical, f.predicate_family or f.metric_canonical, f.normalized_unit)


class CrossDocumentMatcher:
    @classmethod
    def match_facts(cls, facts: List[Fact]) -> List[FactRelationship]:
        buckets: Dict[Tuple[str, str, str], List[Fact]] = defaultdict(list)
        for f in facts:
            if _eligible(f):
                buckets[_key(f)].append(f)
        out: List[FactRelationship] = []
        for group in buckets.values():
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    rel = DeterministicReasoningEngine.evaluate_fact_pair(group[i], group[j])
                    if rel:
                        out.append(rel)
        return out

    @classmethod
    def match_incremental(cls, new_facts: List[Fact], existing_facts: List[Fact]) -> List[FactRelationship]:
        """Compare new facts against existing ones (and among themselves) without touching existing pairs."""
        index: Dict[Tuple[str, str, str], List[Fact]] = defaultdict(list)
        for f in existing_facts:
            if _eligible(f):
                index[_key(f)].append(f)
        out: List[FactRelationship] = []
        fresh = [f for f in new_facts if _eligible(f)]
        for i, nf in enumerate(fresh):
            for ef in index.get(_key(nf), []):
                rel = DeterministicReasoningEngine.evaluate_fact_pair(nf, ef)
                if rel:
                    out.append(rel)
            for of in fresh[i + 1:]:
                if _key(of) == _key(nf):
                    rel = DeterministicReasoningEngine.evaluate_fact_pair(nf, of)
                    if rel:
                        out.append(rel)
        return out
