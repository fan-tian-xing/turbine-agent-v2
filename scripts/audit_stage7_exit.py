"""Run the focused Stage 7 exit gate and write one compact audit record."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from turbine_kg.terminology.analyzer import analyze_terminology, load_stage6_evidence_bundle, load_terminology_contract
from turbine_kg.terminology.models import CANDIDATE_TYPES
from turbine_kg.terminology.validation import content_fingerprint, validate_candidates, validate_input_manifest


ROOT = Path(__file__).resolve().parents[1]
STAGE5 = ROOT / "data" / "stage5"
STAGE6 = ROOT / "data" / "stage6"
STAGE7 = ROOT / "data" / "stage7"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_index() -> dict[str, dict]:
    return {row["asset_id"]: row for row in _jsonl(ROOT / "data" / "registry" / "source_assets.jsonl")}


def _run_tests() -> dict:
    command = [sys.executable, "-m", "pytest", "-q"]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": "src"},
        text=True,
        capture_output=True,
    )
    return {
        "command": " ".join(command),
        "project_python": sys.executable,
        "status": "passed" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
    }


def _critical_sample_metrics(golden: dict, contract: dict, candidates: list[dict]) -> dict:
    """Execute the independent fixture set through the real analyzer."""
    positive_results = []
    for item in golden.get("positive_examples", []):
        page_id = item["example_id"]
        page = {
            "page_id": page_id, "document_key": page_id, "document_logical_id": page_id,
            "revision_id": page_id, "processing_asset_id": "fixture-ocr",
            "authority_asset_id": "fixture-original", "physical_page": 1,
            "page_status": "text_accepted", "text_source": "ocr_processing_text",
            "analysis_text_sha256": "fixture-sha",
        }
        rows = analyze_terminology({"pages": [page]}, {page_id: item["text"]}, contract=contract)
        expected = item["expected"]
        found = any(row["normalized_form"] == expected["normalized_form"] and row["candidate_type"] == expected["candidate_type"] for row in rows)
        positive_results.append({"category": item["category"], "found": found, "example_id": page_id})
    negative_results = []
    for item in golden.get("negative_examples", []):
        page_id = item["example_id"]
        page = {
            "page_id": page_id, "document_key": page_id, "document_logical_id": page_id,
            "revision_id": page_id, "processing_asset_id": "fixture-ocr",
            "authority_asset_id": "fixture-original", "physical_page": 1,
            "page_status": "text_accepted", "text_source": "ocr_processing_text",
            "analysis_text_sha256": "fixture-sha",
        }
        rows = analyze_terminology({"pages": [page]}, {page_id: item["text"]}, contract=contract)
        expected = item["expected"]
        leaked = any(row["normalized_form"] == expected["normalized_form"] and row["candidate_type"] == expected["candidate_type"] for row in rows)
        negative_results.append({"example_id": page_id, "leaked": leaked})
    categories = sorted({item["category"] for item in positive_results})
    by_category = {}
    for category in categories:
        values = [row["found"] for row in positive_results if row["category"] == category]
        by_category[category] = {"found": sum(values), "expected": len(values), "recall": sum(values) / len(values)}
    real_results = []
    for item in golden.get("real_examples", []):
        expected = item["expected"]
        found = any(
            row["normalized_form"] == expected["normalized_form"]
            and row["candidate_type"] == expected["candidate_type"]
            and any(occurrence.get("source_kind") == expected["source_kind"] for occurrence in row["occurrences"])
            for row in candidates
        )
        real_results.append({"category": item["category"], "found": found, "example_id": item["example_id"]})
    real_categories = sorted({item["category"] for item in real_results})
    real_by_category = {}
    for category in real_categories:
        values = [row["found"] for row in real_results if row["category"] == category]
        real_by_category[category] = {"found": sum(values), "expected": len(values), "recall": sum(values) / len(values)}
    return {
        "synthetic": {"overall": {"found": sum(row["found"] for row in positive_results), "expected": len(positive_results), "recall": sum(row["found"] for row in positive_results) / len(positive_results)}, "by_category": by_category},
        "real": {"status": "evaluated", "overall": {"found": sum(row["found"] for row in real_results), "expected": len(real_results), "recall": sum(row["found"] for row in real_results) / len(real_results) if real_results else None}, "by_category": real_by_category},
        "negative": {"leakage_count": sum(row["leaked"] for row in negative_results), "expected_zero": True},
    }


def main() -> None:
    manifest = _read(STAGE7 / "terminology_input_manifest.json")
    candidates_payload = _read(STAGE7 / "terminology_candidates.json")
    capability = _read(STAGE7 / "business_capability_questions.json")
    sample = _read(STAGE5 / "stage5_sample_manifest.json")
    assets = _asset_index()
    stage6_rows = load_stage6_evidence_bundle(STAGE6 / "stage6_evidence_bundle.jsonl")
    contract = load_terminology_contract(ROOT / "config" / "terminology_contract.json")
    golden = _read(STAGE7 / "stage7_critical_term_golden_set.json")
    validate_input_manifest(manifest)

    pages = manifest["pages"]
    candidates = candidates_payload.get("candidates", [])
    accepted_keys = {(row["document_logical_id"], row["physical_page"]) for row in pages if row["page_status"] == "text_accepted"}
    candidate_validation = validate_candidates(candidates, accepted_keys)
    candidate_by_id = {row["candidate_id"]: row for row in candidates}
    page_by_key = {(row["document_logical_id"], row["physical_page"]): row for row in pages}
    authority_profiles_ok = all(
        page.get("authority_source_profile_id") == assets.get(page["authority_asset_id"], {}).get("source_profile_id")
        for page in pages
    )
    metadata_rows = manifest.get("round_1_metadata_discovery", [])
    metadata_discovery_ok = (
        len(metadata_rows) == manifest["input_boundary"].get("source_count")
        and all(
            row.get("authority_asset_id") in assets
            and assets[row["authority_asset_id"]].get("asset_kind") == "original"
            and row.get("registered_name")
            and row.get("registered_name_source") == assets[row["authority_asset_id"]].get("title_source")
            and row.get("directory_discovery", {}).get("status") == "verified_registry_record"
            and row.get("directory_discovery", {}).get("physical_pages_invented") is False
            for row in metadata_rows
        )
    )

    sample_document_keys = {row["document_key"] for row in sample["documents"]}
    admitted_originals = {
        asset_id for asset_id, asset in assets.items()
        if asset.get("asset_kind") == "original" and asset.get("admission_status") == "admitted"
    }
    sample_scope_ok = (
        sample_document_keys == set(manifest["input_boundary"].get("admitted_document_keys", []))
        and set(manifest["input_boundary"].get("admitted_original_asset_ids", [])).issubset(admitted_originals)
    )

    evidence_by_id = {row["evidence"]["evidence_id"]: row for row in stage6_rows}
    canonical_evidence_ok = all(
        all(
            evidence_by_id.get(evidence_id, {}).get("document_key") == page["document_key"]
            and evidence_by_id[evidence_id]["input"]["physical_page"] == page["physical_page"]
            and evidence_by_id[evidence_id]["evidence"].get("review_status") == "accepted"
            and evidence_by_id[evidence_id]["evidence"].get("disposition") == "structured"
            for evidence_id in page.get("stage6_evidence_ids", [])
        )
        for page in pages
        if page["text_source"] == "stage6_accepted_evidence"
    )
    accepted_sources_ok = all(
        page["text_source"] in {"native_pdf_text", "ocr_processing_text", "stage6_accepted_evidence"}
        for page in pages
        if page["page_status"] == "text_accepted"
    )
    table_isolated = all(not page.get("table_candidate") or page["page_status"] in {"visual_only", "excluded_non_content"} for page in pages)
    traceability_ok = all(
        (occurrence["document_logical_id"], occurrence["physical_page"]) in accepted_keys
        for candidate in candidates
        for occurrence in candidate["occurrences"]
    )
    candidate_fingerprints = all(
        content_fingerprint({key: value for key, value in row.items() if key != "content_fingerprint"}) == row["content_fingerprint"]
        for row in candidates
    )
    occurrence_counts_ok = all(
        row["occurrence_count"] == len(row["occurrences"])
        and row["document_frequency"] == len({item["document_logical_id"] for item in row["occurrences"]})
        and row["document_occurrence_counts"] == {
            document_key: sum(item["document_key"] == document_key for item in row["occurrences"])
            for document_key in row["document_occurrence_counts"]
        }
        for row in candidates
    )
    accepted_pages_by_doc = defaultdict(set)
    family_by_doc = {}
    for page in pages:
        if page["page_status"] == "text_accepted":
            accepted_pages_by_doc[page["document_key"]].add(page["physical_page"])
            family_by_doc[page["document_key"]] = page.get("authority_source_profile_id") or page["document_logical_id"]
    def expected_family_score(candidate):
        occurrence_pages = defaultdict(set)
        for occurrence in candidate["occurrences"]:
            occurrence_pages[occurrence["document_key"]].add(occurrence["physical_page"])
        families = sorted(set(family_by_doc.values()))
        family_values = []
        for family in families:
            docs = [doc for doc, profile in family_by_doc.items() if profile == family]
            family_values.append(sum(len(occurrence_pages.get(doc, set())) / len(accepted_pages_by_doc[doc]) for doc in docs) / len(docs))
        return round(sum(family_values) / len(family_values), 6) if family_values else 0.0
    family_scores_ok = all(round(float(row["family_weighted_score"]), 6) == expected_family_score(row) for row in candidates)
    ocr_confirmation_ok = all(
        not (
            row["requires_original_confirmation"] is False
            and row["text_origins"] == ["ocr_text"]
            and all(occurrence["source_kind"] != "accepted_stage6_evidence" for occurrence in row["occurrences"])
        )
        for row in candidates
    )
    capability_questions = capability.get("questions", [])
    capability_shape_ok = (
        len(capability_questions) == 10
        and len({row.get("question_id") for row in capability_questions}) == 10
        and all(row.get("question_template") and row.get("required_slots") for row in capability_questions)
    )
    critical_metrics = _critical_sample_metrics(golden, contract, candidates)
    critical_golden_ok = (
        len(golden.get("positive_examples", [])) == 36
        and len(golden.get("negative_examples", [])) == 12
        and critical_metrics["synthetic"]["overall"]["recall"] == 1.0
        and all(value["recall"] == 1.0 for value in critical_metrics["synthetic"]["by_category"].values())
        and critical_metrics["negative"]["leakage_count"] == 0
        and critical_metrics["real"]["overall"]["recall"] == 1.0
    )
    tests = _run_tests()
    status_counts = manifest["status_counts"]
    expected_counts_ok = status_counts == {
        "text_accepted": 726,
        "visual_only": 39,
        "quarantined": 2,
        "excluded_non_content": 8,
    }
    structural_checks = {
        "input_manifest_frozen": manifest.get("status") == "frozen",
        "all_775_pages_have_one_status": len(pages) == 775 and sum(status_counts.values()) == 775,
        "expected_page_status_counts": expected_counts_ok,
        "only_five_admitted_units": manifest["input_boundary"].get("source_count") == 5 and manifest["input_boundary"].get("unauthorized_source_count") == 0 and sample_scope_ok,
        "case_holdout_blind_materials_explicitly_excluded": set(manifest["input_boundary"].get("excluded_source_classes", {})) == {"formal_case_materials", "holdout_materials", "blind_test_materials"} and bool(manifest["input_boundary"].get("exclusion_enforcement")),
        "only_accepted_pages_are_consumed": accepted_sources_ok and traceability_ok,
        "family_profile_snapshot_matches_original_registry": authority_profiles_ok,
        "round_one_metadata_discovery_is_registry_bound": metadata_discovery_ok,
        "table_pages_are_isolated": table_isolated,
        "stage6_sample_cross_check_is_canonical": canonical_evidence_ok,
        "candidate_types_are_controlled": all(row["candidate_type"] in CANDIDATE_TYPES for row in candidates),
        "candidate_fingerprints_stable": candidate_fingerprints,
        "candidate_occurrence_counts_reconcile": occurrence_counts_ok,
        "family_scores_reconcile": family_scores_ok,
        "ocr_confirmation_boundary_is_explicit": ocr_confirmation_ok,
        "candidate_only_no_promotion": candidates_payload.get("status") == "candidate_only" and candidates_payload.get("automatic_promotion") is False,
        "formal_release_false": candidates_payload.get("formal_release") is False and capability.get("formal_release") is False,
        "runtime_contract_fingerprint_recorded": candidates_payload.get("contract_sha256") == _sha(ROOT / "config" / "terminology_contract.json"),
        "capability_questions_complete": capability_shape_ok,
        "critical_term_golden_set_recall": critical_golden_ok,
        "test_suite_passed": tests["status"] == "passed",
        "formal_artifacts_have_runtime_consumers": bool(candidates_payload.get("consumer")) and bool(manifest.get("consumer")),
    }
    failures = [name for name, passed in structural_checks.items() if not passed]
    status = "complete" if not failures else "blocked"
    audit = {
        "schema_version": 1,
        "stage": "7",
        "artifact_kind": "stage7_exit_audit",
        "status": status,
        "scope": "candidate terminology and business capability templates only",
        "formal_release": False,
        "producer": "scripts/audit_stage7_exit.py",
        "inputs": {
            "terminology_input_manifest": "data/stage7/terminology_input_manifest.json",
            "terminology_candidates": "data/stage7/terminology_candidates.json",
            "business_capability_questions": "data/stage7/business_capability_questions.json",
        },
        "counts": {
            "page_count": len(pages),
            "text_accepted_page_count": status_counts["text_accepted"],
            "visual_only_page_count": status_counts["visual_only"],
            "quarantined_page_count": status_counts["quarantined"],
            "excluded_non_content_page_count": status_counts["excluded_non_content"],
            "ocr_candidate_page_count": sum(row["text_source"] == "ocr_processing_text" and row["page_status"] == "text_accepted" for row in pages),
            "table_candidate_page_count": sum(bool(row.get("table_candidate")) for row in pages),
            "candidate_count": len(candidates),
            "capability_question_count": len(capability_questions),
        },
        "candidate_type_counts": {candidate_type: sum(row["candidate_type"] == candidate_type for row in candidates) for candidate_type in sorted(CANDIDATE_TYPES)},
        "unobserved_declared_candidate_types": sorted(set(contract["candidate_types"]) - {row["candidate_type"] for row in candidates}),
        "candidate_scope": {
            "stage6_evidence_is_sample_cross_check_only": True,
            "ocr_is_candidate_discovery_input_only": True,
            "full_document_evidence_claim": False,
            "engineering_statements_created": False,
            "owl_or_neo4j_changes_created": False,
            "release_created": False,
        },
        "checks": structural_checks,
        "failures": failures,
        "validation": tests,
        "critical_term_metrics": critical_metrics,
        "review_summary": {
            "candidate_count": len(candidates),
            "candidate_only_count": sum(row["review_status"] == "candidate_only" for row in candidates),
            "requires_original_confirmation_count": sum(row["requires_original_confirmation"] for row in candidates),
            "review_decisions_present": 0,
            "human_review_required_now": False,
        },
        "user_review_required_now": [],
        "review_boundary": "Stage 7 emits candidate_only terms. Stage 8 must review only candidates selected for ontology mapping; OCR-only candidates require original-page confirmation before promotion.",
        "next_stage_allowed": not failures,
        "next_stage": "Stage 8 minimal OWL ontology design" if not failures else None,
    }
    (STAGE7 / "stage7_exit_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "checks": len(structural_checks), "failures": failures, "candidate_count": len(candidates)}, ensure_ascii=False))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
