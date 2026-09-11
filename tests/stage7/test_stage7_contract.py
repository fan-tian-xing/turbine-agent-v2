import json
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_capability_question_templates_cover_the_planned_ten_questions():
    payload = json.loads((ROOT / "data/stage7/business_capability_questions.json").read_text(encoding="utf-8"))
    questions = payload["questions"]
    assert payload["status"] == "candidate_templates_only"
    assert payload["formal_release"] is False
    assert len(questions) == 10
    assert len({row["question_id"] for row in questions}) == 10
    assert all(row["question_template"] and row["required_slots"] for row in questions)


def test_terminology_contract_forbids_promotion_and_table_cell_inference():
    contract = json.loads((ROOT / "config/terminology_contract.json").read_text(encoding="utf-8"))
    assert contract["text_acceptance"]["original_asset_is_authority"] is True
    assert contract["text_acceptance"]["unresolved_table_cells_are_excluded"] is True
    assert contract["promotion"]["automatic_ontology_change"] is False
    assert contract["promotion"]["automatic_display_vocabulary_change"] is False
