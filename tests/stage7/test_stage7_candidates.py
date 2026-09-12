import json
from pathlib import Path

from turbine_kg.terminology.models import CANDIDATE_TYPES
from turbine_kg.terminology.analyzer import analyze_terminology
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
    assert all(row["document_weighting_policy"] == "admitted_document_equal_weight_discovery_only" for row in candidates)
    assert all("family_weighted_score" not in row and "family_score_breakdown" not in row for row in candidates)


def test_candidate_fingerprints_and_origins_are_stable():
    candidates = _read("terminology_candidates.json")["candidates"]
    assert len(candidates) > 0
    assert all(
        content_fingerprint({key: value for key, value in row.items() if key != "content_fingerprint"}) == row["content_fingerprint"]
        for row in candidates
    )
    assert all(set(row["text_origins"]) <= {"native_text", "ocr_text"} for row in candidates)
    assert all(isinstance(row["is_ocr_variant"], bool) for row in candidates)


def test_ocr_candidates_keep_semantic_types_and_confirmation_boundary():
    candidates = _read("terminology_candidates.json")["candidates"]
    ocr_candidates = [row for row in candidates if row["text_origins"] == ["ocr_text"]]
    assert ocr_candidates
    assert any(row["candidate_type"] in {"equipment", "component", "action", "process", "verification"} for row in ocr_candidates)
    assert all(
        row["requires_original_confirmation"] is True
        for row in ocr_candidates
        if all(occurrence["source_kind"] != "accepted_stage6_evidence" for occurrence in row["occurrences"])
    )
    assert all(0 <= row["document_equal_weighted_score"] <= 1 for row in candidates)
    assert not {row["normalized_form"] for row in candidates} & {"的规定", "术要求", "要求", "规定"}


def test_explicit_synonym_and_old_name_patterns_produce_source_bound_candidates():
    text = "汽封环又称密封环。调节阀旧称控制阀。"
    pages = []
    texts = {}
    for number in (1,):
        page_id = f"fixture-{number}"
        page = {
            "page_id": page_id,
            "document_key": "fixture",
            "document_logical_id": "doc-fixture",
            "revision_id": "rev-fixture",
            "processing_asset_id": "asset-original",
            "authority_asset_id": "asset-original",
            "physical_page": number,
            "page_status": "text_accepted",
            "text_source": "native_pdf_text",
            "analysis_text_sha256": "fixture-sha",
        }
        pages.append(page)
        texts[page_id] = text
    candidates = analyze_terminology(
        {"pages": pages},
        texts,
    )
    relations = {row["candidate_type"]: row for row in candidates if row["candidate_type"] in {"synonym_candidate", "old_name_candidate"}}
    assert relations["synonym_candidate"]["relation"]["left"] == "汽封环"
    assert relations["synonym_candidate"]["relation"]["right"] == "密封环"
    assert relations["old_name_candidate"]["relation"]["left"] == "调节阀"
    assert relations["old_name_candidate"]["relation"]["right"] == "控制阀"
    assert all(row["occurrences"][0]["physical_page"] == 1 for row in relations.values())


def test_document_equal_weighted_score_gives_each_document_equal_influence():
    pages = []
    texts = {}
    for document_key, page_number, text, profile in (
        ("long", 1, "汽轮机", "manufacturer_manual"),
        ("long", 2, "密封瓦检查", "manufacturer_manual"),
        ("short", 1, "汽轮机", "manufacturer_manual"),
        ("other", 1, "密封瓦检查", "book"),
    ):
        page_id = f"{document_key}-{page_number}"
        pages.append({
            "page_id": page_id,
            "document_key": document_key,
            "document_logical_id": f"doc-{document_key}",
            "revision_id": f"rev-{document_key}",
            "processing_asset_id": f"asset-{document_key}",
            "authority_asset_id": f"asset-{document_key}",
            "physical_page": page_number,
            "page_status": "text_accepted",
            "text_source": "native_pdf_text",
            "analysis_text_sha256": "fixture-sha",
            "authority_source_profile_id": profile,
        })
        texts[page_id] = text
    candidates = analyze_terminology({"pages": pages}, texts)
    turbine = next(row for row in candidates if row["normalized_form"] == "汽轮机" and row["candidate_type"] == "equipment")
    assert turbine["document_occurrence_counts"] == {"long": 1, "short": 1}
    assert turbine["document_equal_weighted_score"] == 0.5
    assert turbine["document_equal_weighted_score_breakdown"] == {"long": 0.5, "other": 0.0, "short": 1.0}


def test_numeric_units_keep_decimal_and_local_critical_context():
    page = {
        "page_id": "numeric-fixture", "document_key": "fixture", "document_logical_id": "doc-fixture",
        "revision_id": "rev-fixture", "processing_asset_id": "asset-original", "authority_asset_id": "asset-original",
        "physical_page": 1, "page_status": "text_accepted", "text_source": "native_pdf_text", "analysis_text_sha256": "fixture-sha",
    }
    candidates = analyze_terminology({"pages": [page]}, {"numeric-fixture": "压力应不大于0.2MPa，间隙为0.03mm。"})
    values = {row["normalized_form"]: row for row in candidates if row["candidate_type"] == "parameter"}
    assert "0.2MPa" in values
    assert "0.03mm" in values
    assert "2MPa" not in values
    assert "03mm" not in values
    assert "comparator" in values["0.2MPa"]["occurrences"][0]["critical_signals"]


def test_ocr_variant_signal_is_term_local():
    page = {
        "page_id": "ocr-fixture", "document_key": "fixture", "document_logical_id": "doc-fixture",
        "revision_id": "rev-fixture", "processing_asset_id": "asset-ocr", "authority_asset_id": "asset-original",
        "physical_page": 1, "page_status": "text_accepted", "text_source": "ocr_processing_text", "analysis_text_sha256": "fixture-sha",
    }
    candidates = analyze_terminology({"pages": [page]}, {"ocr-fixture": "汽轮机检查。误字圧出现在别处。"})
    assert not any(row["candidate_type"] == "ocr_variant_candidate" for row in candidates)
