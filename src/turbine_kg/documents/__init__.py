"""Ontology-independent document parsing structures."""

from .models import DocumentIR
from .validation import validate_document_ir

__all__ = ["DocumentIR", "validate_document_ir"]
