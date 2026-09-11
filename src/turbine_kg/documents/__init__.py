"""Ontology-independent document parsing structures."""

from .catalog import AssetIdentity, IdentityCatalog, load_identity_catalog
from .models import DocumentIR, ManualCorrection
from .ocr_review import apply_reviewed_ocr_boxes
from .page_identity import apply_page_identity
from .table_review import apply_reviewed_table_regions
from .validation import validate_document_ir

__all__ = [
    "AssetIdentity",
    "DocumentIR",
    "IdentityCatalog",
    "ManualCorrection",
    "apply_reviewed_ocr_boxes",
    "apply_page_identity",
    "apply_reviewed_table_regions",
    "load_identity_catalog",
    "validate_document_ir",
]
