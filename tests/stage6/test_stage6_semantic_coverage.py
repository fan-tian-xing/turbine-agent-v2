import copy
import json
import sys
from pathlib import Path


ROOT = Path(__file__).parents[2]
STAGE6 = ROOT / "data" / "stage6"
sys.path.insert(0, str(ROOT / "scripts"))
from audit_stage6_semantic_coverage import _validate_structure, build_audit  # noqa: E402
from build_stage6_golden_evidence import _stage12_extractability  # noqa: E402


def _bundle_by_id():
    rows = [json.loads(line) for line in (STAGE6 / "stage6_evidence_bundle.jsonl").read_text(encoding="utf-8").splitlines() if line]
    return {row["evidence"]["evidence_id"]: row for row in rows}


def test_semantic_coverage_allows_only_complete_reviewed_pages():
    persisted = json.loads((STAGE6 / "stage6_semantic_coverage_audit.json").read_text(encoding="utf-8"))
    assert build_audit() == persisted
    assert persisted["page_count"] == 36
    assert persisted["complete_page_count"] == 28
    assert persisted["excluded_nonsemantic_page_count"] == 8
    assert persisted["complete_page_count"] + persisted["excluded_nonsemantic_page_count"] == persisted["page_count"]
    assert persisted["unresolved_page_count"] == 0
    assert persisted["confirmed_gap_page_count"] == 0
    assert persisted["excluded_incomplete_source_count"] == 5
    assert persisted["source_document_completeness"]["DLT863"]["missing_printed_body_page_count"] == 15
    assert persisted["extractability_counts"]["pending_review"] == 0
    assert persisted["extractability_counts"]["context_only"] == sum(
        row.get("stage12_extractability") == "context_only" for row in _bundle_by_id().values()
    )
    assert persisted["status"] == "complete"
    assert persisted["stage12_input_allowed"] is True
    assert persisted["errors"] == []
    by_page = {(row["document_key"], row["physical_page"]): row for row in persisted["page_results"]}
    assert by_page["D300N", 50]["status"] == "complete"
    assert by_page["HAF103", 29]["status"] == "complete"
    assert by_page["D300N", 73]["status"] == "complete"
    assert by_page["D300N", 38]["status"] == "complete"
    assert by_page["auxiliary_installation_book", 300]["status"] == "complete"


def test_extractability_reads_canonical_row_top_level_and_excludes_context():
    bundle = [json.loads(line) for line in (STAGE6 / "stage6_evidence_bundle.jsonl").read_text(encoding="utf-8").splitlines() if line]
    pending = [row for row in bundle if row.get("stage12_extractability") == "pending_review"]
    context = [row for row in bundle if row.get("stage12_extractability") == "context_only"]
    assert pending == []
    assert context
    assert all("stage12_extractability" not in row["evidence"] for row in context)
    assert all(row.get("extractability_reason") for row in context)
    audit = build_audit()
    assert audit["extractability_counts"]["pending_review"] == len(pending)
    assert audit["extractability_counts"]["context_only"] == len(context)
    assert audit["stage12_input_allowed"] is True


def test_reviewed_source_group_requires_every_answer_to_be_extractable():
    index = json.loads((STAGE6 / "stage6_source_structure_index.json").read_text(encoding="utf-8"))
    evidence = _bundle_by_id()
    groups = [group for group in index["context_groups"] if group["group_kind"] == "question_answer"]
    assert groups and _validate_structure(index, evidence) == []
    altered = copy.deepcopy(evidence)
    answer_id = groups[0]["answer_evidence_ids"][-1]
    altered[answer_id]["stage12_extractability"] = "context_only"
    assert any("invalid_question_answer_group" in error for error in _validate_structure(index, altered))


def test_generic_malformed_ocr_tokens_need_review_before_extraction():
    sample = {"document_key": "new_document", "physical_page": 1}
    overrides = {"extractability_decisions": []}
    assert _stage12_extractability(sample, "Te99999999R", overrides)[0] == "pending_review"
    assert _stage12_extractability(sample, "Pamb-H2=760--100", overrides)[0] == "pending_review"
    assert _stage12_extractability(sample, "支撑条间隙应符合原图", overrides) == (None, None)


def test_reviewed_operation_steps_bind_to_current_evidence_and_reject_bad_quote():
    index = json.loads((STAGE6 / "stage6_source_structure_index.json").read_text(encoding="utf-8"))
    evidence = _bundle_by_id()
    assert _validate_structure(index, evidence) == []
    confirmed = next(group for group in index["operation_groups"] if group["source_review_status"] == "confirmed")
    assert confirmed["source_total_steps"] == 6
    assert [step["source_order"] for step in confirmed["steps"]] == [1, 2, 3, 4, 5, 6]
    assert all(step["evidence_ids"] and step["source_quotes"] for step in confirmed["steps"])
    altered = copy.deepcopy(index)
    altered_step = altered["operation_groups"][0]["steps"][2]
    altered_step["source_quotes"][altered_step["evidence_ids"][0]] = "原页没有的操作"
    assert any("step_source_mismatch" in error for error in _validate_structure(altered, evidence))


def test_reviewed_question_answer_is_bound_to_marked_option_text():
    index = json.loads((STAGE6 / "stage6_source_structure_index.json").read_text(encoding="utf-8"))
    evidence = _bundle_by_id()
    questions = [group for group in index["context_groups"] if group["group_kind"] == "question_options"]
    assert len(questions) == 15
    assert all(group["answer_source_quote"] in evidence[group["answer_evidence_id"]]["evidence"]["effective_text"] for group in questions)
    altered = copy.deepcopy(index)
    question = next(group for group in altered["context_groups"] if group.get("question_id") == "La5A1006")
    question["answer_text"] = "1000"
    assert any("invalid_question_options_group" in error for error in _validate_structure(altered, evidence))
