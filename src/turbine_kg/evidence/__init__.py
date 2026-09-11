"""Source-grounded evidence construction and review."""

from .builder import build_evidence
from .coordinates import pixel_bbox_to_pdf_bbox, union_pixel_boxes
from .models import Evidence, EvidenceBundle, EvidenceLocation, FigureContext, TableContext
from .validation import validate_evidence_bundle

__all__ = [
    "Evidence",
    "EvidenceBundle",
    "EvidenceLocation",
    "FigureContext",
    "TableContext",
    "build_evidence",
    "pixel_bbox_to_pdf_bbox",
    "union_pixel_boxes",
    "validate_evidence_bundle",
]
