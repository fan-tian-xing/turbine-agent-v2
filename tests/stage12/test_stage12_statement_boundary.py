import json
from pathlib import Path

from turbine_kg.extraction.semantic import HeuristicSemanticExtractor, _classify_unmatched_candidate


ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "data/stage11/stage11_statement_development_samples.jsonl"
PROMPT = ROOT / "config/stage12_prompt.txt"


def _gold_rows():
    return [json.loads(line) for line in GOLD.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_current_gold_uses_canonical_semantically_isomorphic_delivery_statement():
    rows = _gold_rows()
    ids = {row["statement_id"] for row in rows}
    canonical = next(row for row in rows if row["statement_id"] == "stage12-gold-delivery-07")
    assert "stage12-gold-delivery-08" not in ids
    assert "stage12-gold-delivery-09" not in ids
    assert len(canonical["entity_alignment"]) == 3
    assert {item["surface_form"] for item in canonical["entity_alignment"]} == {
        "厂房内各基础的纵横中心线",
        "厂房内各基础的标高标识",
        "厂房内各基础的基础沉降观测点",
    }


def test_current_gold_negation_is_source_grounded():
    rows = {row["statement_id"]: row for row in _gold_rows()}
    assert rows["stage12-gold-seal-vertical-joint-flatness"]["negation_scope"] == [
        {"surface_form": "无错口", "polarity": "negative", "scope_type": "statement"}
    ]
    assert rows["stage12-gold-seal-axial-paint-check"]["negation_scope"] == []


def test_prompt_states_semantic_boundary_regression_examples():
    prompt = PROMPT.read_text(encoding="utf-8")
    assert "semantically isomorphic" in prompt
    assert "substantive difference" in prompt
    assert "never decide the boundary by themselves" in prompt
    assert "multiple entities" in prompt
    assert "independently retrievable" in prompt


def test_causal_antecedent_is_not_duplicated_as_condition():
    text = "真空过低会给汽缸和转子造成较大的热冲击。"
    evidence = {
        "evidence_id": "boundary-causal",
        "document_logical_id": "fixture",
        "revision_id": "revision",
        "physical_page": 1,
        "source_span_id": "span",
        "source_text_sha256": "x",
        "source_text": text,
        "review_status": "accepted",
    }
    candidate = HeuristicSemanticExtractor().extract(evidence)[0]
    assert candidate["predicate"] == "causes"
    assert candidate["relation_direction"] == "cause_to_effect"
    assert candidate["conditions"] == []


def test_exclusion_negation_is_preserved_deterministically():
    evidence = {
        "evidence_id": "exclusion-evidence", "document_logical_id": "fixture", "revision_id": "revision",
        "physical_page": 1, "source_span_id": "span", "source_text_sha256": "x",
        "source_text": "本规定不涉及工业安全。", "review_status": "accepted",
    }
    candidate = HeuristicSemanticExtractor().extract(evidence)[0]
    assert {item["surface_form"] for item in candidate["negation_scope"]} == {"不涉及"}


def test_gold_audit_keeps_local_condition_and_activity_applicability_distinct():
    rows = {row["statement_id"]: row for row in _gold_rows()}
    staged = rows["stage11-statement-5740cf642266fcd6feb4"]
    assert staged["applicability_scope"]["applicability_text"] == "当调试活动分阶段实施时"
    assert staged["conditions"][0]["surface_form"].endswith("监查，并确认调试结果评价满足了全部核安全管理要求之后")
    assert rows["stage12-gold-seal-axial-paint-check"]["conditions"] == []


def test_gold_parallel_entities_are_not_collapsed_into_compound_nouns():
    rows = {row["statement_id"]: row for row in _gold_rows()}
    assert {item["surface_form"] for item in rows["stage12-gold-oil-cleanliness"]["entity_alignment"]} == {"油室", "油孔"}
    assert {item["surface_form"] for item in rows["stage11-statement-a9ff60d2526222447813-s5"]["entity_alignment"]} >= {"汽缸", "转子"}
    assert "相关过程" not in rows["stage11-statement-a9ff60d2526222447813-s5"]["statement_text"]


def test_unmatched_substatement_is_classified_as_over_split():
    base = {"candidate_id": "base", "statement_text": "perform inspection and ensure the contact surface is uniform and continuous"}
    extra = {"candidate_id": "extra", "statement_text": "ensure the contact surface is uniform and continuous"}
    assert _classify_unmatched_candidate("extra", [base, extra], {0: base}, []) == "over_split"
