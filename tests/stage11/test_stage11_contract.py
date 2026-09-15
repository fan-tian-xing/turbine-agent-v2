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


def test_stage11_exit_stays_blocked_until_semantic_gold_review():
    entry = _read("data/stage11/stage11_entry_audit.json")
    assert entry["status"] == "in_progress"
    assert entry["next_stage_allowed"] is False
    assert entry["checks"]["stage12_entry_allowed"] is False
    assert "holdout_semantic_review_complete" in entry["blockers"]
    assert entry["sample_registry"]["development_regression_golden"]["page_count"] == 36
    assert entry["sample_registry"]["acceptance_holdout"]["page_count"] == 15
    assert "adjudication_complete" in entry["blockers"]


def test_stage11_development_samples_are_source_grounded_and_cover_five_documents():
    rows = _jsonl("data/stage11/stage11_statement_development_samples.jsonl")
    assert len(rows) >= 9
    assert {row["document_key"] for row in rows} == {"DL5190.3", "D300N", "DLT863", "HAF103", "auxiliary_installation_book"}
    assert len({row["statement_id"] for row in rows}) == len(rows)
    assert all(row["review_status"] in {"accepted", "pending_manual_review"} and row["formal_release"] is False for row in rows)
    assert all(row["evidence_bindings"] and row["subject_entity_id"] and row["source_text_sha256"] and row["object_value"] and row["applicability_scope"] for row in rows)


def test_stage11_holdout_is_fifteen_pages_three_per_document_and_disjoint():
    rows = _jsonl("data/stage11/stage11_statement_holdout.jsonl")
    dev = _jsonl("data/stage11/stage11_statement_development_samples.jsonl")
    assert len(rows) == 15
    assert {row["document_key"] for row in rows} == {"DL5190.3", "D300N", "DLT863", "HAF103", "auxiliary_installation_book"}
    assert all(sum(row["document_key"] == doc for row in rows) == 3 for doc in {row["document_key"] for row in rows})
    assert not ({(row["document_key"], row["physical_page"]) for row in rows} & {(row["document_key"], row["physical_page"]) for row in dev})
    assert all(row["independent_for_statement"] is True and row["review_status"] in {"pending_manual_review", "isolated"} for row in rows)
    assert all(len(row["review_plan"]) == 2 and {item["round"] for item in row["review_plan"]} == {1, 2} for row in rows)


def test_stage11_registry_has_one_frozen_entrypoint_and_blind_is_unread():
    registry = _read("data/stage11/evaluation_sample_registry.json")
    assert len(registry["records"]) == 56
    assert sum(r["split"] == "development_regression_golden" for r in registry["records"]) == 36
    assert sum(r["split"] == "acceptance_holdout" for r in registry["records"]) == 15
    assert sum(r["split"] == "acceptance_holdout_reserve" for r in registry["records"]) == 5
    assert registry["development"]["trial_page_subset_count"] == 15
    assert registry["blind_test"]["read_by_stage11"] is False


def test_stage11_semantic_candidates_do_not_promote_numbers_or_entities():
    rows = _jsonl("data/stage11/stage11_statement_holdout.jsonl")
    p13 = next(row for row in rows if row["document_key"] == "DLT863" and row["physical_page"] == 13)
    assert p13["review_status"] == "pending_manual_review"
    assert p13["value"] is None and p13["unit"] is None and p13["quantities"] == []
    assert p13["entity_alignment"][0]["entity_class"] == "UnresolvedEntityCandidate"
    p429 = next(row for row in rows if row["document_key"] == "auxiliary_installation_book" and row["physical_page"] == 429)
    assert p429["review_status"] == "isolated"
    assert p429["review_status"] == "isolated"


def test_stage11_confirmed_development_rows_retain_numeric_and_negation_annotations():
    rows = _jsonl("data/stage11/stage11_statement_development_samples.jsonl")[:4]
    assert any(row["quantities"] for row in rows if row["value"] is not None)
    assert any(row["negation_scope"] for row in rows if "不入" in row["statement_text"])


def test_stage11_statement_classifier_does_not_use_single_character_hou_as_procedure():
    from scripts.build_stage11_holdout import _statement_type
    assert _statement_type("修改后的试验和检查，营运单位应当全面试验") == "requirement"


def test_stage11_does_not_read_blind_or_legacy_inputs():
    entry = _read("data/stage11/stage11_entry_audit.json")
    assert "旧demo" not in json.dumps(entry["inputs"], ensure_ascii=False)
    assert "blind_test" not in json.dumps(entry["inputs"], ensure_ascii=False)
    assert entry["sample_registry"]["blind_test"]["read_by_stage11"] is False


def test_stage11_two_review_rounds_are_independent_and_unresolved_until_adjudication():
    review_a = _jsonl("data/stage11/review_round_a.jsonl")
    review_b = _jsonl("data/stage11/review_round_b.jsonl")
    queue = _jsonl("data/stage11/stage11_adjudication_queue.jsonl")
    assert review_a and review_b and queue
    assert {row["reviewer_id"] for row in review_a} == {"reviewer_a"}
    assert {row["reviewer_id"] for row in review_b} == {"reviewer-b"}
    assert all((row.get("candidate_unchanged") is True or row.get("original_sample_untouched") is True) and row["input_sha256"] and row["output_sha256"] for row in review_a + review_b)
    assert all(row["adjudication_status"] != "adjudicated" for row in queue)


def test_stage12_input_gate_is_fail_closed():
    contract = _read("config/stage11_statement_contract.json")
    gate = contract["stage12_input_gate"]
    assert gate["sample_tier"] == "gold"
    assert gate["label_status"] == "gold"
    assert gate["review_status"] == "accepted"
    assert gate["independent_review_required"] is True
    assert {"candidate_only", "pending_manual_review", "isolated", "rejected"} <= set(gate["disallowed_statuses"])
