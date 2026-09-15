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


def test_stage11_exit_is_complete_and_opens_stage12():
    entry = _read("data/stage11/stage11_entry_audit.json")
    assert entry["status"] == "complete"
    assert entry["next_stage_allowed"] is True
    assert entry["checks"]["stage12_entry_allowed"] is True
    assert entry["sample_registry"]["development_regression_golden"]["page_count"] == 36
    assert entry["sample_registry"]["acceptance_holdout"]["page_count"] == 15


def test_stage11_development_samples_are_source_grounded_and_cover_five_documents():
    rows = _jsonl("data/stage11/stage11_statement_development_samples.jsonl")
    assert len(rows) >= 9
    assert {row["document_key"] for row in rows} == {"DL5190.3", "D300N", "DLT863", "HAF103", "auxiliary_installation_book"}
    assert len({row["statement_id"] for row in rows}) == len(rows)
    assert all(row["review_status"] == "accepted" and row["formal_release"] is False for row in rows)
    assert all(row["evidence_bindings"] and row["subject_entity_id"] and row["source_text_sha256"] and row["object_value"] and row["applicability_scope"] for row in rows)


def test_stage11_holdout_is_fifteen_pages_three_per_document_and_disjoint():
    rows = _jsonl("data/stage11/stage11_statement_holdout.jsonl")
    dev = _jsonl("data/stage11/stage11_statement_development_samples.jsonl")
    assert len(rows) == 15
    assert {row["document_key"] for row in rows} == {"DL5190.3", "D300N", "DLT863", "HAF103", "auxiliary_installation_book"}
    assert all(sum(row["document_key"] == doc for row in rows) == 3 for doc in {row["document_key"] for row in rows})
    assert not ({(row["document_key"], row["physical_page"]) for row in rows} & {(row["document_key"], row["physical_page"]) for row in dev})
    assert all(row["independent_for_statement"] is True and row["reviewer_type"] == "ai_cross_review" for row in rows)
    assert all(len(row["review_rounds"]) == 2 and all(review["status"] == "accepted" for review in row["review_rounds"]) for row in rows)


def test_stage11_registry_has_one_frozen_entrypoint_and_blind_is_unread():
    registry = _read("data/stage11/evaluation_sample_registry.json")
    assert len(registry["records"]) == 56
    assert sum(r["split"] == "development_regression_golden" for r in registry["records"]) == 36
    assert sum(r["split"] == "acceptance_holdout" for r in registry["records"]) == 15
    assert sum(r["split"] == "acceptance_holdout_reserve" for r in registry["records"]) == 5
    assert registry["development"]["trial_page_subset_count"] == 15
    assert registry["blind_test"]["read_by_stage11"] is False


def test_stage11_does_not_read_blind_or_legacy_inputs():
    entry = _read("data/stage11/stage11_entry_audit.json")
    assert "旧demo" not in json.dumps(entry["inputs"], ensure_ascii=False)
    assert "blind_test" not in json.dumps(entry["inputs"], ensure_ascii=False)
    assert entry["sample_registry"]["blind_test"]["read_by_stage11"] is False
