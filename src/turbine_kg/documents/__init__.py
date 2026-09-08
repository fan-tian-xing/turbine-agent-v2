"""Ontology-independent document parsing structures."""

from .catalog import AssetIdentity, IdentityCatalog, load_identity_catalog
from .models import DocumentIR, ManualCorrection
from .validation import validate_document_ir

__all__ = [
    "AssetIdentity",
    "DocumentIR",
    "IdentityCatalog",
    "ManualCorrection",
    "load_identity_catalog",
    "validate_document_ir",
]
