import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _jsonl(path):
    return [json.loads(line) for line in (ROOT / path).read_text(encoding="utf-8").splitlines() if line.strip()]


def test_stage11_contract_has_task_specific_boundary_and_normalized_fields():
    contract = _read("config/stage11_statement_contract.json")
    assert contract["stage"] == "11"
    assert contract["formal_release"] is False
    assert set(contract["sample_split"]) == {"development_regression_golden", "acceptance_holdout", "blind_test"}
    assert {"statement_id", "statement_text", "subject_entity_id", "predicate", "object_value", "evidence_bindings", "applicability_scope", "review_status", "source_text_sha256"} <= set(contract["statement_fields"])
    assert contract["requirements"]["holdout_isolation_is_task_specific"] is True
    assert contract["requirements"]["holdout_is_not_used_for_statement_tuning"] is True
    assert contract["requirements"]["adjudication_hash_matches_final_gold"] is True
    assert contract["requirements"]["adjudication_statement_count_matches_final_gold"] is True


def test_stage11_exit_opens_after_semantic_gold_review():
    entry = _read("data/stage11/stage11_entry_audit.json")
    assert entry["status"] == "complete"
    assert entry["next_stage_allowed"] is True
    assert entry["checks"]["stage12_entry_allowed"] is True
    assert entry["blockers"] == []
    assert entry["sample_registry"]["development_regression_golden"]["page_count"] == 36
    assert entry["sample_registry"]["acceptance_holdout"]["page_count"] == 15
    assert entry["checks"]["adjudication_complete"] is True
    assert entry["checks"]["review_independence"] is True


def test_stage11_has_a_distinct_unified_exit_audit():
    exit_audit = _read("data/stage11/stage11_exit_audit.json")
    assert exit_audit["status"] == "complete"
    assert exit_audit["next_stage_allowed"] is True
    assert exit_audit["zero_tolerance_errors"] == []
    assert exit_audit["outputs"]["entry_audit"] == "data/stage11/stage11_entry_audit.json"
    assert exit_audit["outputs"]["exit_audit"] == "data/stage11/stage11_exit_audit.json"
    assert exit_audit["test_result"]["targeted"]["status"] == "passed"
    assert exit_audit["next_stage_inputs"]["development_gold"] == "data/stage11/stage11_statement_development_samples.jsonl"


def test_stage11_development_samples_are_source_grounded_and_cover_five_documents():
    rows = _jsonl("data/stage11/stage11_statement_development_samples.jsonl")
    assert len({row["sample_id"] for row in rows}) == 10
    assert {row["document_key"] for row in rows} == {"DL5190.3", "D300N", "DLT863", "HAF103", "auxiliary_installation_book"}
    assert len({row["statement_id"] for row in rows}) == len(rows)
    assert all(row["review_status"] in {"accepted", "pending_manual_review", "isolated"} and row["formal_release"] is False for row in rows)
    assert all(row["evidence_bindings"] and row["subject_entity_id"] and row["source_text_sha256"] and row["object_value"] and row["applicability_scope"] for row in rows)


def test_stage11_holdout_is_fifteen_pages_three_per_document_and_disjoint():
    rows = _jsonl("data/stage11/stage11_statement_holdout.jsonl")
    dev = _jsonl("data/stage11/stage11_statement_development_samples.jsonl")
    registry = _read("data/stage11/evaluation_sample_registry.json")
    selected_pages = {(row["document_key"], row["physical_page"]) for row in registry["records"] if row["split"] == "acceptance_holdout"}
    statement_pages = {(row["document_key"], row["physical_page"]) for row in rows}
    assert len(selected_pages) == 15
    assert all(sum(page[0] == doc for page in selected_pages) == 3 for doc in {"DL5190.3", "D300N", "DLT863", "HAF103", "auxiliary_installation_book"})
    assert statement_pages <= selected_pages
    assert not (selected_pages & {(row["document_key"], row["physical_page"]) for row in dev})
    assert all(row["independent_for_statement"] is True and row["review_status"] in {"accepted", "pending_manual_review", "isolated"} for row in rows)
    assert all(len(row["review_plan"]) == 2 and {item["round"] for item in row["review_plan"]} == {1, 2} for row in rows)


def test_stage11_registry_has_one_frozen_entrypoint_and_blind_is_unread():
    registry = _read("data/stage11/evaluation_sample_registry.json")
    assert len(registry["records"]) == 56
    assert sum(r["split"] == "development_regression_golden" for r in registry["records"]) == 36
    assert sum(r["split"] == "acceptance_holdout" for r in registry["records"]) == 15
    assert sum(r["split"] == "acceptance_holdout_reserve" for r in registry["records"]) == 5
    assert registry["development"]["trial_page_subset_count"] == 15
    assert registry["blind_test"]["read_by_stage11"] is False


def test_stage11_gold_rows_preserve_numeric_and_entity_annotations():
    rows = _jsonl("data/stage11/stage11_statement_holdout.jsonl")
    p13 = [row for row in rows if row["document_key"] == "DLT863" and row["physical_page"] == 13]
    assert len(p13) == 10
    assert all(row["review_status"] == "accepted" and row["label_status"] == "gold" for row in p13)
    assert any(row["quantities"] for row in p13)
    p429 = next(row for row in rows if row["document_key"] == "auxiliary_installation_book" and row["physical_page"] == 429)
    assert p429["review_status"] == "isolated"
    assert p429["review_status"] == "isolated"
    p78 = [row for row in rows if row["document_key"] == "auxiliary_installation_book" and row["physical_page"] == 78]
    assert len(p78) == 6
    assert all(row["review_status"] == "accepted" and row["label_status"] == "gold" and row["answer_injection"]["selected_option"] for row in p78)
    assert "下列四种形式中" in next(row for row in p78 if row["answer_injection"]["question_id"] == "Lb1A5327")["statement_text"]
    assert p78[0]["quantities"] == [{"surface_form": "0.08~0.10 mm", "min": 0.08, "max": 0.10, "unit": "mm", "operator": "range"}]
    assert {entity["surface_form"] for entity in p78[0]["entity_alignment"]} == {"大型立式循环水泵", "转子上导轴瓦"}


def test_stage11_comparison_direction_matches_chinese_bound_semantics():
    from scripts.audit_stage11_exit import validate_statement_semantics

    rows = _jsonl("data/stage11/stage11_statement_holdout.jsonl")
    row = next(row for row in rows if row["document_key"] == "D300N" and row["physical_page"] == 61)
    bounds = {item["surface_form"]: item for item in row["quantities"]}
    assert bounds["不小于工作齿长的60%"]["operator"] == "gte"
    first_bound = next(item for item in row["negation_scope"] if item["surface_form"] == "不小于工作齿长的60%")
    assert first_bound["polarity"] == "lower_bound"
    assert first_bound["scope"] == "对应参数下限"
    broken = json.loads(json.dumps(row, ensure_ascii=False))
    broken["quantities"][0]["operator"] = "lte"
    broken["negation_scope"][0]["polarity"] = "upper_bound"
    assert validate_statement_semantics(broken)["comparison_direction_matches_text"] is False
    broken = json.loads(json.dumps(row, ensure_ascii=False))
    broken["negation_scope"][0]["scope"] = "对应参数上限"
    assert validate_statement_semantics(broken)["comparison_direction_matches_text"] is False


def test_stage11_confirmed_development_rows_retain_numeric_and_negation_annotations():
    rows = _jsonl("data/stage11/stage11_statement_development_samples.jsonl")[:4]
    assert any(row["quantities"] for row in rows if row["value"] is not None)
    assert any(row["negation_scope"] for row in rows if "不入" in row["statement_text"])


def test_stage11_accepted_new_gold_rows_bind_two_review_rounds():
    rows = _jsonl("data/stage11/stage11_statement_development_samples.jsonl") + _jsonl("data/stage11/stage11_statement_holdout.jsonl")
    accepted_new = [row for row in rows if row["review_status"] == "accepted" and row.get("review_basis") != "stage3_user_confirmation"]
    assert accepted_new
    assert all(len(row.get("review_rounds", [])) == 2 for row in accepted_new)
    assert all({round_["reviewer_id"] for round_ in row["review_rounds"]} == {"reviewer_a", "reviewer-b"} for row in accepted_new)
    assert all(all(round_["status"] == "accepted" and round_["input_sha256"] and round_["output_sha256"] for round_ in row["review_rounds"]) for row in accepted_new)


def test_stage11_statement_classifier_does_not_use_single_character_hou_as_procedure():
    from scripts.build_stage11_holdout import _statement_type
    assert _statement_type("修改后的试验和检查，营运单位应当全面试验") == "requirement"


def test_stage11_does_not_read_blind_or_legacy_inputs():
    entry = _read("data/stage11/stage11_entry_audit.json")
    assert "旧demo" not in json.dumps(entry["inputs"], ensure_ascii=False)
    assert "blind_test" not in json.dumps(entry["inputs"], ensure_ascii=False)
    assert entry["sample_registry"]["blind_test"]["read_by_stage11"] is False


def test_stage11_two_review_rounds_are_independent_and_adjudicated():
    review_a = _jsonl("data/stage11/review_round_a.jsonl")
    review_b = _jsonl("data/stage11/review_round_b.jsonl")
    queue = _jsonl("data/stage11/stage11_adjudication_queue.jsonl")
    assert review_a and review_b and queue
    assert {row["reviewer_id"] for row in review_a} == {"reviewer_a"}
    assert {row["reviewer_id"] for row in review_b} == {"reviewer-b"}
    assert all((row.get("candidate_unchanged") is True or row.get("original_sample_untouched") is True) and row["input_sha256"] and row["output_sha256"] for row in review_a + review_b)
    assert all(row["adjudication_status"] == "adjudicated" for row in queue)
    assert next(row for row in queue if row["sample_id"] == "stage11-holdout-e1af768dfb83126d80da")["adjudication_result"] == "accepted"
    target_ids = {row["sample_id"] for row in queue}
    assert target_ids <= {row["sample_id"] for row in review_a}
    assert target_ids <= {row["sample_id"] for row in review_b}


def test_stage11_adjudication_hash_and_count_match_final_gold_rows():
    from scripts.audit_stage11_exit import _final_gold_hash, _final_gold_rows

    statements = _jsonl("data/stage11/stage11_statement_development_samples.jsonl") + _jsonl("data/stage11/stage11_statement_holdout.jsonl")
    queue = _jsonl("data/stage11/stage11_adjudication_queue.jsonl")
    for item in queue:
        sample_id = item["sample_id"]
        assert item["adjudicated_statement_count"] == len(_final_gold_rows(statements, sample_id))
        assert item["adjudication_output_sha256"] == _final_gold_hash(statements, sample_id)


def test_stage12_input_gate_is_fail_closed():
    contract = _read("config/stage11_statement_contract.json")
    gate = contract["stage12_input_gate"]
    assert gate["sample_tier"] == "gold"
    assert gate["label_status"] == "gold"
    assert gate["review_status"] == "accepted"
    assert gate["independent_review_required"] is True
    assert {"candidate_only", "pending_manual_review", "isolated", "rejected"} <= set(gate["disallowed_statuses"])
