"""Read immutable manual-correction overlays for a parsed Document IR."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from turbine_kg.documents.models import DocumentIR, ManualCorrection
from turbine_kg.documents.validation import validate_document_ir


_REQUIRED_FIELDS = {
    "correction_id",
    "block_version_id",
    "original_text",
    "corrected_text",
    "reason",
    "reviewer",
    "reviewed_at",
}


def load_manual_corrections(path: Path, document_ir: DocumentIR) -> tuple[ManualCorrection, ...]:
    """Load a JSONL review overlay without mutating parser-produced blocks.

    The caller supplies the exact IR run being reviewed; validation rejects a
    stale overlay whose target block or original text no longer matches it.
    """
    corrections: list[ManualCorrection] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        row = json.loads(raw_line)
        if not isinstance(row, dict) or not _REQUIRED_FIELDS <= row.keys():
            raise ValueError(f"invalid manual-correction overlay record at line {line_number}")
        corrections.append(ManualCorrection(
            correction_id=row["correction_id"],
            block_version_id=row["block_version_id"],
            original_text=row["original_text"],
            corrected_text=row["corrected_text"],
            reason=row["reason"],
            reviewer=row["reviewer"],
            reviewed_at=row["reviewed_at"],
            status=row.get("status", "accepted"),
        ))
    return validate_document_ir(replace(document_ir, manual_corrections=tuple(corrections))).manual_corrections
