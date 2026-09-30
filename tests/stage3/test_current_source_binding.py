"""Source migration must not turn old user approval into approval of new facts."""

from copy import deepcopy
import hashlib
import json

import pytest

from turbine_kg.stage3 import real_trial
from turbine_kg.ontology.research_adapter import validate_research_documents
from turbine_kg.stage3.projection import build_traceability_projection


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def bound_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(real_trial, "PROJECT_ROOT", tmp_path)
    old_quote = "大修半空缸状态下抬起前箱0.1～0.15mm，应不入。"
    current_quote = "大修半空缸状态下抬起前箱０.１～０.１５mm,应不入."
    item = {
        "evidence_id": "confirmed-evidence", "statement_id": "confirmed-statement",
        "source_span_id": "historical-span", "object_id": "front_box",
        "statement_type": "maintenance_procedure", "statement_text": old_quote,
        "quote": old_quote, "quantities": [{"min": 0.1, "max": 0.15, "unit": "mm"}],
        "applicability": {"model": "N-300", "condition": "half_cylinder_state"},
    }
    group = {
        "group_id": "confirmed-group", "page_id": "historical-page", "span_id": "historical-span",
        "document_logical_id": "doc", "revision_id": "rev", "asset_id": "ocr-asset",
        "relative_path": "OCR/source.pdf", "sha256": "a" * 64, "pdf_page_number": 73,
        "logical_page": "3-5-1", "title": "Synthetic source", "source_role": "manufacturer_manual",
        "source_scope": {"model": "N-300", "condition": "half_cylinder_state"},
        "review_status": "confirmed", "user_confirmation": True, "evidence": [item],
    }
    confirmation = {"status": "confirmed_by_user", "confirmed_groups": [group]}
    runtime = {"pages": [{**group, "asset_sha256": group["sha256"], "span_preview": old_quote}]}
    registry = {
        "asset_id": "ocr-asset", "document_logical_id": "doc", "revision_id": "rev",
        "relative_path": "OCR/source.pdf", "sha256": "b" * 64,
        "applicability_scope": ["N-300"], "applicability_scope_structured": {"model": "N-300"},
    }
    confirmation_path, runtime_path = tmp_path / "confirmation.json", tmp_path / "runtime.json"
    registry_path = tmp_path / "data/registry/source_assets.jsonl"
    registry_path.parent.mkdir(parents=True)
    _write(confirmation_path, confirmation)
    _write(runtime_path, runtime)
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    text = "完整来源前文" * 100 + current_quote + "完整来源后文"
    binding = {
        "schema_version": 1, "artifact_kind": "stage9_current_source_bindings",
        "status": "current_source_reviewed", "formal_release": False, "user_confirmation": False,
        "historical_confirmation_sha256": _sha(confirmation_path),
        "historical_runtime_sha256": _sha(runtime_path), "registry_sha256": _sha(registry_path),
        "groups": [{
            "group_id": group["group_id"],
            "historical": {field: group[field] for field in (
                "page_id", "span_id", "document_logical_id", "revision_id", "asset_id",
                "relative_path", "sha256", "pdf_page_number", "logical_page",
            )},
            "current": {
                **{field: registry[field] for field in ("asset_id", "document_logical_id", "revision_id", "relative_path", "sha256")},
                "page_id": "current-page", "pdf_page_number": 73, "logical_page": "3-5-1",
                "source_span": {"span_id": "current-span", "page_id": "current-page", "text": text,
                                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "complete": True},
            },
            "evidence": [{
                "evidence_id": item["evidence_id"], "statement_id": item["statement_id"],
                "historical_quote": old_quote, "historical_item_sha256": real_trial.confirmed_item_sha256(item),
                "current_quote": current_quote,
                "semantic_review": {"status": "same", "reviewer": "synthetic AI source reviewer",
                                    "reviewer_type": "ai_source_review", "reviewed_at": "2026-09-28T00:00:00+08:00",
                                    "basis": "Synthetic fixture, not review of production material.", "user_confirmation": False,
                                    "checks": {field: "same" for field in ("quote_support", "quantity", "negation", "direction", "condition")},
                                    "conflicts": []},
            }],
        }],
    }
    binding_path = tmp_path / "binding.json"
    _write(binding_path, binding)
    return confirmation_path, runtime_path, registry_path, binding_path, binding


def test_explicit_current_binding_preserves_semantics_and_complete_span(bound_sources):
    confirmation, runtime, _, binding_path, binding = bound_sources
    before = (confirmation.read_bytes(), runtime.read_bytes())
    (document,) = real_trial.load_confirmed_real_corpus(confirmation, runtime, current_binding_path=binding_path)
    assert document.spans[0].quote == binding["groups"][0]["current"]["source_span"]["text"]
    assert len(document.spans[0].quote) > 360
    assert document.evidence[0].source_span_ids == ("current-span",)
    assert document.evidence[0].text == binding["groups"][0]["evidence"][0]["current_quote"]
    assert document.statements[0].text == binding["groups"][0]["evidence"][0]["historical_quote"]
    assert document.statements[0].quantities == ((0.1, 0.15, "mm"),)
    assert document.statements[0].scope.condition == "half_cylinder_state"
    assert (confirmation.read_bytes(), runtime.read_bytes()) == before
    report = validate_research_documents((document,))
    assert report["conforms"], report["failures"]
    projection = build_traceability_projection((document,))
    assert any(node["id"] == "confirmed-statement" for node in projection["nodes"])


def test_present_binding_file_does_not_relax_omitted_argument(bound_sources):
    confirmation, runtime, _, _, _ = bound_sources
    with pytest.raises(ValueError, match="differs from Registry"):
        real_trial.load_confirmed_real_corpus(confirmation, runtime)


def test_unmigrated_historical_path_retains_original_contract(bound_sources):
    confirmation, runtime, registry_path, _, _ = bound_sources
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["sha256"] = "a" * 64
    _write(registry_path, registry)
    (document,) = real_trial.load_confirmed_real_corpus(confirmation, runtime)
    assert document.spans[0].span_id == "historical-span"


@pytest.mark.parametrize("field", ["historical_confirmation_sha256", "historical_runtime_sha256", "registry_sha256"])
def test_stale_binding_fingerprints_are_rejected(bound_sources, field):
    confirmation, runtime, _, path, binding = bound_sources
    binding[field] = "0" * 64
    _write(path, binding)
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        real_trial.load_confirmed_real_corpus(confirmation, runtime, current_binding_path=path)


@pytest.mark.parametrize("change", ["group", "statement", "historical_quote", "item_hash", "asset", "page", "span_hash", "incomplete", "duplicate"])
def test_fabricated_or_incomplete_binding_is_rejected(bound_sources, change):
    confirmation, runtime, _, path, binding = bound_sources
    group = binding["groups"][0]
    if change == "group": group["group_id"] = "not-confirmed"
    elif change == "statement": group["evidence"][0]["statement_id"] = "not-confirmed"
    elif change == "historical_quote": group["evidence"][0]["historical_quote"] = "fabricated"
    elif change == "item_hash": group["evidence"][0]["historical_item_sha256"] = "0" * 64
    elif change == "asset": group["current"]["sha256"] = "0" * 64
    elif change == "page": group["current"]["pdf_page_number"] = 74
    elif change == "span_hash": group["current"]["source_span"]["text_sha256"] = "0" * 64
    elif change == "incomplete": group["current"]["source_span"]["complete"] = False
    else: binding["groups"].append(deepcopy(group))
    _write(path, binding)
    with pytest.raises(ValueError):
        real_trial.load_confirmed_real_corpus(confirmation, runtime, current_binding_path=path)


@pytest.mark.parametrize("dimension", ["quantity", "negation", "direction", "condition"])
def test_recorded_critical_conflict_remains_blocked(bound_sources, dimension):
    confirmation, runtime, _, path, binding = bound_sources
    review = binding["groups"][0]["evidence"][0]["semantic_review"]
    review["checks"][dimension] = "conflict"
    review["conflicts"] = [dimension]
    _write(path, binding)
    with pytest.raises(ValueError, match="semantic review"):
        real_trial.load_confirmed_real_corpus(confirmation, runtime, current_binding_path=path)


@pytest.mark.parametrize("old,new", [("０.１５", "０.１６"), ("应不入", "应入"), ("抬起", "放下"), ("半空缸", "满缸")])
def test_claiming_same_cannot_approve_changed_facts(bound_sources, old, new):
    confirmation, runtime, _, path, binding = bound_sources
    group = binding["groups"][0]
    group["evidence"][0]["current_quote"] = group["evidence"][0]["current_quote"].replace(old, new)
    span = group["current"]["source_span"]
    span["text"] = span["text"].replace(old, new)
    span["text_sha256"] = hashlib.sha256(span["text"].encode("utf-8")).hexdigest()
    _write(path, binding)
    with pytest.raises(ValueError, match="changes confirmed wording"):
        real_trial.load_confirmed_real_corpus(confirmation, runtime, current_binding_path=path)


@pytest.mark.parametrize("change", ["missing", "new_user_confirmation", "not_actual_source_review", "absent_quote"])
def test_source_review_and_direct_quote_support_are_required(bound_sources, change):
    confirmation, runtime, _, path, binding = bound_sources
    evidence = binding["groups"][0]["evidence"][0]
    if change == "missing": evidence.pop("semantic_review")
    elif change == "new_user_confirmation": evidence["semantic_review"]["user_confirmation"] = True
    elif change == "not_actual_source_review": evidence["semantic_review"]["reviewer_type"] = "automatic_matching"
    else: evidence["current_quote"] = "This is not present on the current page."
    _write(path, binding)
    with pytest.raises(ValueError):
        real_trial.load_confirmed_real_corpus(confirmation, runtime, current_binding_path=path)
