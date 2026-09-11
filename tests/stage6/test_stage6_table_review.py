import json
from pathlib import Path


ROOT = Path(__file__).parents[2]
STAGE6 = ROOT / "data" / "stage6"


def _jsonl(name):
    return [
        json.loads(line)
        for line in (STAGE6 / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_six_quarantined_pages_are_resolved_as_seven_region_evidence_records():
    audit = json.loads((STAGE6 / "stage6_table_evidence_audit.json").read_text(encoding="utf-8"))
    rows = _jsonl("stage6_table_evidence_annotations.jsonl")
    assert audit["status"] == "complete"
    assert audit["reviewed_page_count"] == 6
    assert audit["table_region_count"] == 7
    assert audit["remaining_quarantined_page_count"] == 0
    assert len(rows) == 7
    assert all(row["evidence"]["review_status"] == "accepted" for row in rows)
    assert all(row["evidence"]["disposition"] == "region_scoped" for row in rows)


def test_table_region_context_keeps_headers_continuation_and_no_data_cell_claims():
    rows = _jsonl("stage6_table_evidence_annotations.jsonl")
    by_label = {
        row["evidence"]["table_context"][0]["table_label"]: row
        for row in rows
    }
    assert len(by_label) == 7
    for row in rows:
        context = row["evidence"]["table_context"][0]
        assert context["review_scope"] == "table_region"
        assert context["leaf_column_count"] == len(context["header_cell_ids"])
        assert context["header_hierarchy"]
        assert context["value_cell_ids"] == []
        assert row["evidence"]["authority_asset_id"] == row["evidence"]["locations"][0]["original_asset_id"]
    assert by_label["续表2-14-1"]["evidence"]["table_context"][0]["continuation_from_physical_page"] == 49
    assert by_label["续表2-14-1"]["evidence"]["table_context"][0]["continuation_to_physical_page"] == 51
    assert by_label["表D.1（续）"]["evidence"]["table_context"][0]["continuation_from_physical_page"] == 27
    assert by_label["表D.2"]["evidence"]["table_context"][0]["leaf_column_count"] == 13


def test_stage6_exit_audit_closes_all_36_pages_without_formal_release():
    audit = json.loads((STAGE6 / "stage6_exit_audit.json").read_text(encoding="utf-8"))
    assert audit["status"] == "complete"
    assert audit["formal_release"] is False
    assert audit["golden_sample_page_count"] == 36
    assert audit["accepted_text_page_count"] == 22
    assert audit["accepted_table_page_count"] == 6
    assert audit["negative_gate_page_count"] == 8
    assert audit["accepted_evidence_count"] == 287
    assert audit["structured_table_data_cell_count"] == 0
    assert audit["unresolved_review_count"] == 0
    assert all(audit["checks"].values())
