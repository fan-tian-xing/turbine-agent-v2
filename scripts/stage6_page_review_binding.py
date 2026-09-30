"""Shared binding of a page review to the exact current Evidence versions and inputs."""

import hashlib
import json
from pathlib import Path

from stage5_fingerprint import sha256_file


STAGE5_BINDING_PATHS = {
    "stage5_sample_manifest_sha256": "data/stage5/stage5_sample_manifest.json",
    "source_assets_sha256": "data/registry/source_assets.jsonl",
    "full_corpus_reviews_sha256": "data/registry/ocr_validation_report.json",
    "stage5_truth_annotations_sha256": "data/stage5/stage5_truth_annotations_2026-09-28.json",
    "stage5_table_truth_review_sha256": "data/stage5/stage5_table_truth_review.json",
}


def current_stage5_bindings(root: Path, source_input_fingerprint: str) -> dict[str, str]:
    return {"source_input_fingerprint": source_input_fingerprint,
            **{key: sha256_file(root / path) for key, path in STAGE5_BINDING_PATHS.items()}}


def input_bindings_match(payloads, expected: dict[str, str]) -> bool:
    return all(payload.get("inputs", {}).get(key) == value
               for payload in payloads for key, value in expected.items())


def page_review_fingerprint(items, *, source_input_fingerprint: str) -> str:
    if not source_input_fingerprint:
        raise ValueError("page review requires a current source input fingerprint")
    fields = ("evidence_id", "evidence_version_id", "authority_asset_id", "processing_asset_id")
    records = [{field: (item[field] if isinstance(item, dict) else getattr(item, field)) for field in fields}
               for item in items]
    material = {"binding_version": "reviewed-pdf-page-v2", "source_input_fingerprint": source_input_fingerprint,
                "evidence": records}
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
