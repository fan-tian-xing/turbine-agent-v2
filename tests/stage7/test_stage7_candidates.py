import json
from pathlib import Path

from turbine_kg.terminology.models import CANDIDATE_TYPES
from turbine_kg.terminology.validation import content_fingerprint, validate_candidates


ROOT = Path(__file__).parents[2]


def _read(name):
    return json.loads((ROOT / "data/stage7" / name).read_text(encoding="utf-8"))


def test_candidates_are_traceable_and_candidate_only():
    manifest = _read("terminology_input_manifest.json")
    payload = _read("terminology_candidates.json")
    accepted = {(p["document_logical_id"], p["physical_page"]) for p in manifest["pages"] if p["page_status"] == "text_accepted"}
    candidates = payload["candidates"]
    validate_candidates(candidates, accepted)
    assert payload["status"] == "candidate_only"
    assert payload["formal_release"] is False
    assert payload["automatic_promotion"] is False
    assert all(row["candidate_type"] in CANDIDATE_TYPES for row in candidates)
    assert all(row["review_status"] == "candidate_only" for row in candidates)
    assert all(row["occurrences"] and row["capability_question_ids"] for row in candidates)


def test_candidate_fingerprints_and_origins_are_stable():
    candidates = _read("terminology_candidates.json")["candidates"]
    assert len(candidates) > 0
    assert all(
        content_fingerprint({key: value for key, value in row.items() if key != "content_fingerprint"}) == row["content_fingerprint"]
        for row in candidates
    )
    assert all(set(row["text_origins"]) <= {"native_text", "ocr_text"} for row in candidates)
    assert all(isinstance(row["is_ocr_variant"], bool) for row in candidates)
