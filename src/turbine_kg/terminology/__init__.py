"""Stage 7 candidate terminology analysis.

This package produces discovery candidates only.  It never changes the
display vocabulary, ontology, Evidence, graph projection, or Release data.
"""

from .analyzer import analyze_terminology, load_stage6_evidence_bundle
from .models import CANDIDATE_TYPES, PAGE_STATUSES

__all__ = [
    "CANDIDATE_TYPES",
    "PAGE_STATUSES",
    "analyze_terminology",
    "load_stage6_evidence_bundle",
]
