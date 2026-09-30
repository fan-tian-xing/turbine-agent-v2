"""Source-anchored multi-Evidence operation groups."""

import hashlib
import json
from pathlib import Path

import pytest
from turbine_kg.extraction.semantic import ExtractionProfile, ProfileRouter, _assemble_candidate, to_stage9_runtime_payload, validate_candidate_against_evidence, validate_candidate_evidence_binding
from scripts.build_stage12_candidates import _context_groups, _gate, _page_evidence, _source_groups, summarize_context_groups, summarize_operation_groups, summarize_question_groups

ROOT = Path(__file__).resolve().parents[2]


def test_reviewed_source_lists_restore_title_context_without_document_specific_code():
    rows = [json.loads(line) for line in (ROOT / "data/stage6/stage6_evidence_bundle.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    evidence_by_id = {row["evidence"]["evidence_id"]: row["evidence"] for row in rows}
    manifest = json.loads((ROOT / "data/stage12/stage12_input_manifest.json").read_text(encoding="utf-8"))
    page = next(row for row in manifest["pages"] if row["document_key"] == "DL5190.3" and row["physical_page"] == 14)
    prepared = _page_evidence(page, evidence_by_id, _source_groups(), _context_groups())
    source = prepared["evidence-291b8c352838ac7ecc80"]
    profile = ProfileRouter().route(source)
    candidate = _assemble_candidate({
        "statement_text": "安装专业应参加基座浇灌前的中间核查。",
        "statement_type": "requirement", "predicate": "requires",
        "subject_entities": [{"surface_form": "安装专业", "role": "subject"}],
        "conditions": [], "applicability_scope": {"status": "unknown"},
        "source_list_item": {"group_id": "dl5190-p14-3.1.2-installation-coordination", "status": "indexed", "item_index": 2, "item_total": 4},
    }, source, profile, "development_regression_golden", 1)
    validate_candidate_against_evidence(candidate, source)
    assert any(binding["support_type"] == "context" for binding in candidate["evidence_bindings"])

    activity_page = next(row for row in manifest["pages"] if row["document_key"] == "DLT863" and row["physical_page"] == 14)
    activity = _page_evidence(activity_page, evidence_by_id, _source_groups(), _context_groups())["evidence-f6624d44d000470f19a7"]
    activity_candidate = _assemble_candidate({
        "statement_text": "发电机内冷水系统热交换器投运。",
        "statement_type": "procedure", "predicate": "describes",
        "subject_entities": [{"surface_form": "发电机内冷水系统热交换器", "role": "subject"}],
        "conditions": [], "applicability_scope": {"status": "unknown"},
        "source_list_item": {"group_id": "dlt863-p14-5.2.13.1-cooling-commissioning-items", "status": "indexed", "item_index": 3, "item_total": 7},
    }, activity, ProfileRouter().route(activity), "development_regression_golden", 1)
    validate_candidate_against_evidence(activity_candidate, activity)


def _case(count: int = 5):
    evidence = {}
    steps = []
    candidates = []
    for index in range(1, count + 1):
        evidence_id = f"e{index}"
        quote = f"执行第{index}步操作"
        evidence[evidence_id] = {
            "evidence_id": evidence_id,
            "effective_text": quote,
            "review_status": "accepted",
            "document_logical_id": "doc-a",
            "revision_id": "rev-a",
        }
        steps.append({"source_order": index, "source_step_label": str(index), "evidence_id": evidence_id, "source_span_ids": [f"span-{index}"], "source_quote": quote, "status": "confirmed"})
        candidates.append({
            "candidate_id": f"stage12-candidate-{index:020x}",
            "statement_text": quote,
            "document_logical_id": "doc-a",
            "revision_id": "rev-a",
            "source_span_ids": [f"span-{index}"],
            "evidence_bindings": [{"evidence_id": evidence_id, "support_type": "direct"}],
            "procedure_group": {"group_id": "op-a", "status": "indexed", "step_index": index, "step_total": count},
        })
    group = {
        "group_id": "op-a", "document_logical_id": "doc-a", "revision_id": "rev-a",
        "document_key": "fixture", "physical_pages": [1, 2], "source_title": "操作 A",
        "source_review_status": "confirmed", "source_total_steps": count, "steps": steps,
    }
    return group, candidates, evidence


def _outcome(group, candidates, evidence):
    return summarize_operation_groups([group], candidates, evidence, set(evidence))[0]


def test_five_independent_evidence_steps_form_one_confirmed_cross_page_group():
    group, candidates, evidence = _case()
    assert _outcome(group, candidates, evidence)["status"] == "complete"
    first_page = _page_evidence({"document_key": "fixture", "evidence_ids": ["e1", "e2"]}, evidence, [group])
    context = first_page["e1"]["operation_group_context"][0]
    assert [item["evidence_id"] for item in context["member_evidence"]] == ["e1", "e2", "e3", "e4", "e5"]


def test_confirmed_operation_title_can_ground_step_applicability():
    text = "调整空侧密封油泵出口压力至设计值。"
    evidence = {
        "evidence_id": "step", "source_text": text, "source_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "document_logical_id": "doc-a", "revision_id": "rev-a", "physical_page": 1,
        "source_span_id": "span-step", "review_status": "accepted", "document_key": "fixture",
    }
    title_text = "密封油系统调整（双流环）"
    title = {"evidence_id": "title", "text": title_text, "document_logical_id": "doc-a", "revision_id": "rev-a", "locations": [{"physical_page": 1, "source_span_id": "span-title"}], "evidence_version_id": "title-v1", "source_text_sha256": hashlib.sha256(title_text.encode()).hexdigest()}
    evidence["operation_group_context"] = [{
        "group_id": "op-1", "source_review_status": "confirmed", "source_title": title["text"],
        "title_evidence_id": "title", "member_evidence": [title],
        "steps": [{"source_order": 1, "status": "confirmed", "evidence_id": "step", "source_quote": text, "source_span_ids": ["span-step"]}],
    }]
    profile = ExtractionProfile("construction_standard", "fixture-profile", "standard_or_regulation", ())
    candidate = _assemble_candidate({
        "statement_text": "双流环：" + text, "statement_type": "procedure", "predicate": "describes",
        "subject_entities": [{"surface_form": "空侧密封油泵", "role": "subject"}],
        "conditions": [], "applicability_scope": {"status": "known", "applicability_text": "双流环"},
        "procedure_group": {"group_id": "op-1", "status": "indexed", "step_index": 1, "step_total": 1},
    }, evidence, profile, "development_regression_golden", 1)
    assert validate_candidate_against_evidence(candidate, evidence)["applicability"] is True


def test_missing_step_and_wrong_order_leave_group_unresolved():
    group, candidates, evidence = _case()
    assert _outcome(group, candidates[:-1], evidence)["missing_step_indices"] == [5]
    assert _outcome(group, candidates[:-1], evidence)["status"] == "unresolved"
    assert _outcome(group, list(reversed(candidates)), evidence)["status"] == "unresolved"


def test_adjacent_unrelated_evidence_or_wrong_revision_cannot_complete_group():
    group, candidates, evidence = _case()
    evidence["neighbor"] = {"evidence_id": "neighbor", "effective_text": "另一项无关操作", "review_status": "accepted", "document_logical_id": "doc-a", "revision_id": "rev-a"}
    page = _page_evidence({"document_key": "fixture", "evidence_ids": ["e5", "neighbor"]}, evidence, [group])
    assert page["neighbor"]["operation_group_context"] == []
    mixed = [*candidates[:4], {**candidates[4], "evidence_bindings": [{"evidence_id": "neighbor", "support_type": "direct"}]}]
    assert _outcome(group, mixed, evidence)["status"] == "unresolved"
    wrong_revision = [*candidates[:4], {**candidates[4], "revision_id": "rev-other"}]
    assert _outcome(group, wrong_revision, evidence)["status"] == "unresolved"


def test_unconfirmed_source_group_cannot_be_promoted_by_complete_model_output():
    group, candidates, evidence = _case()
    group["source_review_status"] = "unresolved"
    assert _outcome(group, candidates, evidence)["status"] == "unresolved"


def test_multiple_confirmed_steps_may_share_one_primary_evidence():
    group, candidates, evidence = _case(6)
    text = "；".join(item["source_quote"] for item in group["steps"])
    shared = {**evidence["e1"], "evidence_id": "shared", "effective_text": text}
    for step, candidate in zip(group["steps"], candidates):
        step["evidence_id"] = "shared"
        candidate["evidence_bindings"] = [{"evidence_id": "shared", "support_type": "direct"}]
    outcome = summarize_operation_groups([group], candidates, {"shared": shared}, {"shared"})[0]
    assert outcome["status"] == "complete"


def test_one_step_can_bind_two_confirmed_cross_page_evidence_fragments():
    def evidence(evidence_id, text, page, span):
        return {"evidence_id": evidence_id, "effective_text": text, "source_text": text,
                "source_text_sha256": hashlib.sha256(text.encode()).hexdigest(), "evidence_version_id": f"version-{evidence_id}",
                "review_status": "accepted", "document_logical_id": "doc-a", "revision_id": "rev-a",
                "locations": [{"physical_page": page, "logical_page": str(page), "source_span_id": span}]}

    evidence_by_id = {"e1": evidence("e1", "检查油压时，", 1, "span-1"), "e2": evidence("e2", "依次关闭阀门。", 2, "span-2")}
    group = {"group_id": "op-cross", "document_logical_id": "doc-a", "revision_id": "rev-a", "source_review_status": "confirmed",
             "source_total_steps": 1, "source_title": "阀门操作", "physical_pages": [1, 2],
             "steps": [{"source_order": 1, "evidence_ids": ["e1", "e2"], "source_quote": "检查油压时，依次关闭阀门。",
                        "source_quotes": {"e1": "检查油压时，", "e2": "依次关闭阀门。"}, "source_span_ids": ["span-1", "span-2"], "status": "confirmed"}]}
    prepared = _page_evidence({"document_key": "fixture", "evidence_ids": ["e1"]}, evidence_by_id, [group])["e1"]
    profile = ExtractionProfile("construction_standard", "fixture-profile", "standard_or_regulation", ())
    item = {"statement_text": "检查油压时，依次关闭阀门。", "statement_type": "procedure", "predicate": "describes",
            "subject_entities": [{"surface_form": "阀门", "role": "object"}], "conditions": [], "applicability_scope": {"status": "unknown"},
            "procedure_group": {"group_id": "op-cross", "status": "indexed", "step_index": 1, "step_total": 1},
            "supporting_evidence_ids": ["e2"]}
    candidate = _assemble_candidate(item, prepared, profile, "development_regression_golden", 1)
    assert {binding["evidence_id"] for binding in candidate["evidence_bindings"]} == {"e1", "e2"}
    for canonical in evidence_by_id.values():
        validate_candidate_evidence_binding(candidate, canonical)
        validate_candidate_against_evidence(candidate, canonical)
    to_stage9_runtime_payload([candidate])
    assert summarize_operation_groups([group], [candidate], evidence_by_id, {"e1", "e2"})[0]["status"] == "complete"


def test_prepared_manifest_covers_all_development_pages_without_opening_llm_gate():
    manifest = json.loads((ROOT / "data/stage12/stage12_input_manifest.json").read_text(encoding="utf-8"))
    registry = json.loads((ROOT / "data/stage11/evaluation_sample_registry.json").read_text(encoding="utf-8"))
    registered = {(row["document_logical_id"], row["revision_id"], row["physical_page"]) for row in registry["records"] if row["split"] == "development_regression_golden"}
    listed = {(page["document_logical_id"], page["revision_id"], page["physical_page"]) for page in manifest["pages"]}
    assert listed == registered and len(listed) == 36
    canonical_rows = [json.loads(line) for line in (ROOT / "data/stage6/stage6_evidence_bundle.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    expected_ids = {row["evidence"]["evidence_id"] for row in canonical_rows
                    if row["evidence"].get("review_status") == "accepted"
                    and row["evidence"].get("content_kind") != "table"
                    and row.get("stage12_extractability") not in {"pending_review", "context_only"}
                    and (row["evidence"]["document_logical_id"], row["evidence"]["revision_id"], row["evidence"]["locations"][0]["physical_page"]) in registered}
    actual_ids = [evidence_id for page in manifest["pages"] for evidence_id in page["evidence_ids"]]
    assert set(actual_ids) == expected_ids and len(actual_ids) == len(expected_ids)
    excluded = {evidence_id for page in manifest["pages"] for evidence_id in page["excluded_evidence_ids"]}
    context_only = {evidence_id for page in manifest["pages"] for evidence_id in page["context_only_evidence_ids"]}
    for row in canonical_rows:
        evidence_id = row["evidence"]["evidence_id"]
        page_key = (
            row["evidence"]["document_logical_id"],
            row["evidence"]["revision_id"],
            row["evidence"]["locations"][0]["physical_page"],
        )
        if page_key not in registered:
            continue
        if row.get("stage12_extractability") == "pending_review":
            assert evidence_id in excluded and evidence_id not in actual_ids
        if row.get("stage12_extractability") == "context_only":
            assert evidence_id in context_only and evidence_id not in actual_ids
    if manifest["status"] == "prepared_pending_upstream":
        with pytest.raises(ValueError):
            _gate(manifest)


def test_five_classes_across_two_evidence_are_not_procedure_steps():
    evidence = {
        "e1": {"evidence_id": "e1", "effective_text": "类别1；类别2；类别3", "review_status": "accepted", "document_logical_id": "doc-a", "revision_id": "rev-a"},
        "e2": {"evidence_id": "e2", "effective_text": "类别4；类别5", "review_status": "accepted", "document_logical_id": "doc-a", "revision_id": "rev-a"},
        "neighbor": {"evidence_id": "neighbor", "effective_text": "另一项无关操作", "review_status": "accepted", "document_logical_id": "doc-a", "revision_id": "rev-a"},
    }
    items, candidates = [], []
    for index in range(1, 6):
        evidence_id = "e1" if index <= 3 else "e2"
        span = "span-1" if index <= 3 else "span-2"
        quote = f"类别{index}"
        items.append({"source_order": index, "source_quote": quote, "source_quotes": {evidence_id: quote},
                      "evidence_ids": [evidence_id], "source_span_ids": [span], "status": "confirmed"})
        candidates.append({"candidate_id": f"stage12-candidate-{index:020x}", "statement_text": quote, "statement_type": "fact",
                           "document_logical_id": "doc-a", "revision_id": "rev-a", "source_span_ids": [span],
                           "evidence_bindings": [{"evidence_id": evidence_id, "support_type": "direct"}],
                           "source_list_item": {"group_id": "classes", "status": "indexed", "item_index": index, "item_total": 5}})
    group = {"group_id": "classes", "group_kind": "classification_list", "document_logical_id": "doc-a", "revision_id": "rev-a",
             "source_review_status": "confirmed", "source_total_items": 5, "member_evidence_ids": ["e1", "e2"], "items": items}
    assert summarize_context_groups([group], candidates, evidence, {"e1", "e2", "neighbor"})[0]["status"] == "complete"
    prepared = _page_evidence({"document_key": "fixture", "evidence_ids": ["e1", "neighbor"]}, evidence, [], [group])
    assert len(prepared["e1"]["related_source_context"][0]["member_evidence"]) == 2
    assert prepared["neighbor"]["related_source_context"] == []
    assert prepared["e1"]["operation_group_context"] == []
    wrong_kind = [*candidates[:4], {**candidates[4], "statement_type": "procedure"}]
    assert summarize_context_groups([group], wrong_kind, evidence, {"e1", "e2"})[0]["status"] == "unresolved"
    assert summarize_context_groups([group], candidates[:-1], evidence, {"e1", "e2"})[0]["missing_item_indices"] == [5]


def test_haf_513_source_anchor_is_classification_not_operation():
    index = json.loads((ROOT / "data/stage6/stage6_source_structure_index.json").read_text(encoding="utf-8"))
    group = next(item for item in index["context_groups"] if item["group_id"] == "haf103-p12-5.1.3-limit-condition-classes")
    assert group["group_kind"] == "classification_list"
    assert group["source_total_items"] == len(group["items"]) == 5
    assert len(group["member_evidence_ids"]) == 5
    assert group["group_id"] not in {item["group_id"] for item in index["operation_groups"]}


def test_marked_question_binds_only_its_confirmed_stem_and_options():
    def evidence(evidence_id, text, span):
        return {"evidence_id": evidence_id, "effective_text": text, "source_text": text,
                "source_text_sha256": hashlib.sha256(text.encode()).hexdigest(), "evidence_version_id": f"version-{evidence_id}",
                "review_status": "accepted", "document_logical_id": "doc-a", "revision_id": "rev-a",
                "locations": [{"physical_page": 32, "logical_page": "32", "source_span_id": span}]}

    evidence_by_id = {"stem": evidence("stem", "长度单位未标注时为（A）。", "span-stem"),
                      "options": evidence("options", "(A) mm；(B) cm。", "span-options"),
                      "neighbor": evidence("neighbor", "另一道题的选项。", "span-neighbor")}
    group = {"group_id": "question-1", "group_kind": "question_options", "document_logical_id": "doc-a", "revision_id": "rev-a",
             "source_review_status": "confirmed", "answer_marked": True, "member_evidence_ids": ["stem", "options"],
             "stem_evidence_id": "stem",
             "answer_option": "A", "answer_text": "mm", "answer_source_quote": "(A) mm", "answer_evidence_id": "options"}
    prepared = _page_evidence({"document_key": "fixture", "evidence_ids": ["stem", "options", "neighbor"]}, evidence_by_id, [], [group])
    profile = ExtractionProfile("construction_standard", "fixture-profile", "standard_or_regulation", ())
    item = {"statement_text": "长度单位未标注时为mm。", "statement_type": "fact", "predicate": "describes",
            "subject_entities": [{"surface_form": "长度单位", "role": "subject"}], "conditions": [], "applicability_scope": {"status": "unknown"},
            "source_question": {"group_id": "question-1", "status": "linked", "answer_option": "A", "answer_text": "mm"}, "supporting_evidence_ids": ["options"]}
    candidate = _assemble_candidate(item, prepared["stem"], profile, "development_regression_golden", 1)
    for canonical in evidence_by_id.values():
        if canonical["evidence_id"] != "neighbor":
            validate_candidate_evidence_binding(candidate, canonical)
            if canonical["evidence_id"] == "stem":
                validate_candidate_against_evidence(candidate, prepared["stem"])
    assert summarize_question_groups([group], [candidate], evidence_by_id, set(evidence_by_id))[0]["status"] == "complete"
    assert prepared["neighbor"]["related_source_context"] == []
    with pytest.raises(ValueError):
        _assemble_candidate({**item, "supporting_evidence_ids": ["neighbor"]}, prepared["stem"], profile, "development_regression_golden", 1)
    with pytest.raises(ValueError, match="printed marked option"):
        _assemble_candidate({**item, "statement_text": "长度单位未标注时为cm。", "source_question": {**item["source_question"], "answer_option": "B", "answer_text": "cm"}}, prepared["stem"], profile, "development_regression_golden", 1)
    with pytest.raises(ValueError, match="does not assert the printed answer"):
        _assemble_candidate({**item, "statement_text": "长度单位未标注时为cm。"}, prepared["stem"], profile, "development_regression_golden", 1)
    unmarked = {**group, "answer_marked": False, "source_review_status": "unresolved"}
    unmarked_prepared = _page_evidence({"document_key": "fixture", "evidence_ids": ["stem"]}, evidence_by_id, [], [unmarked])["stem"]
    with pytest.raises(ValueError):
        _assemble_candidate(item, unmarked_prepared, profile, "development_regression_golden", 1)
    assert summarize_question_groups([unmarked], [], evidence_by_id, set(evidence_by_id))[0]["status"] == "unresolved"


def test_single_evidence_marked_question_excludes_distractor_quantity():
    text = "部件间隙不应超过（C）mm。(A) 1; (B) 2; (C) 3; (D) 4。"
    evidence = {
        "evidence_id": "question", "effective_text": text, "source_text": text,
        "source_text_sha256": hashlib.sha256(text.encode()).hexdigest(), "evidence_version_id": "version-question",
        "review_status": "accepted", "document_logical_id": "doc-a", "revision_id": "rev-a",
        "locations": [{"physical_page": 1, "logical_page": "1", "source_span_id": "span-question"}],
    }
    group = {
        "group_id": "question-1", "group_kind": "question_options", "document_logical_id": "doc-a", "revision_id": "rev-a",
        "source_review_status": "confirmed", "answer_marked": True, "member_evidence_ids": ["question"],
        "stem_evidence_id": "question", "options_evidence_id": "question", "answer_evidence_id": "question",
        "answer_option": "C", "answer_text": "3", "answer_source_quote": "(C) 3",
    }
    prepared = _page_evidence({"document_key": "fixture", "evidence_ids": ["question"]}, {"question": evidence}, [], [group])["question"]
    profile = ExtractionProfile("construction_standard", "fixture-profile", "standard_or_regulation", ())
    base = {
        "statement_type": "requirement", "predicate": "requires",
        "subject_entities": [{"surface_form": "部件间隙", "role": "subject"}],
        "conditions": [], "applicability_scope": {"status": "unknown"},
        "source_question": {"group_id": "question-1", "status": "linked", "answer_option": "C", "answer_text": "3"},
    }
    correct = _assemble_candidate({**base, "statement_text": "部件间隙不应超过3mm。"}, prepared, profile, "development_regression_golden", 1)
    validate_candidate_against_evidence(correct, prepared)
    assert len(correct["evidence_bindings"]) == 1
    wrong = _assemble_candidate({**base, "statement_text": "部件间隙不应超过3mm，另可为4mm。"}, prepared, profile, "development_regression_golden", 2)
    with pytest.raises(ValueError, match="unsupported quantity"):
        validate_candidate_against_evidence(wrong, prepared)


def test_question_candidate_does_not_bind_separate_distractor_evidence():
    def evidence(evidence_id, text):
        return {
            "evidence_id": evidence_id, "effective_text": text, "source_text": text,
            "source_text_sha256": hashlib.sha256(text.encode()).hexdigest(), "evidence_version_id": f"version-{evidence_id}",
            "review_status": "accepted", "document_logical_id": "doc-a", "revision_id": "rev-a",
            "locations": [{"physical_page": 1, "logical_page": "1", "source_span_id": f"span-{evidence_id}"}],
        }
    rows = {
        "stem": evidence("stem", "长度单位未标注时为（A）。"),
        "answer": evidence("answer", "(A) mm"),
        "distractor": evidence("distractor", "(B) cm"),
    }
    group = {
        "group_id": "question-separate-options", "group_kind": "question_options",
        "document_logical_id": "doc-a", "revision_id": "rev-a", "source_review_status": "confirmed",
        "answer_marked": True, "member_evidence_ids": ["stem", "answer", "distractor"],
        "stem_evidence_id": "stem", "answer_evidence_id": "answer",
        "answer_option": "A", "answer_text": "mm", "answer_source_quote": "(A) mm",
    }
    prepared = _page_evidence({"document_key": "fixture", "evidence_ids": list(rows)}, rows, [], [group])["stem"]
    profile = ExtractionProfile("construction_standard", "fixture-profile", "standard_or_regulation", ())
    candidate = _assemble_candidate({
        "statement_text": "长度单位未标注时为mm。", "statement_type": "fact", "predicate": "describes",
        "subject_entities": [{"surface_form": "长度单位", "role": "subject"}],
        "conditions": [], "applicability_scope": {"status": "unknown"},
        "source_question": {"group_id": group["group_id"], "status": "linked", "answer_option": "A", "answer_text": "mm"},
        "supporting_evidence_ids": ["answer"],
    }, prepared, profile, "development_regression_golden", 1)
    assert {binding["evidence_id"] for binding in candidate["evidence_bindings"]} == {"stem", "answer"}
    validate_candidate_against_evidence(candidate, prepared)
    assert summarize_question_groups([group], [candidate], rows, set(rows))[0]["status"] == "complete"


def test_classification_item_requires_reviewed_title_context_binding():
    def evidence(evidence_id, text, span):
        return {
            "evidence_id": evidence_id, "effective_text": text, "source_text": text,
            "source_text_sha256": hashlib.sha256(text.encode()).hexdigest(), "evidence_version_id": f"version-{evidence_id}",
            "review_status": "accepted", "document_logical_id": "doc-a", "revision_id": "rev-a",
            "locations": [{"physical_page": 1, "logical_page": "1", "source_span_id": span}],
        }
    source = {"title": evidence("title", "运行限值和条件可以分为以下一类", "span-title"),
              "item": evidence("item", "（1）安全限值；", "span-item")}
    group = {
        "group_id": "classes", "group_kind": "classification_list", "document_logical_id": "doc-a", "revision_id": "rev-a",
        "source_review_status": "confirmed", "source_title": "运行限值和条件可以分为以下一类",
        "title_evidence_id": "title", "member_evidence_ids": ["item"], "source_total_items": 1,
        "items": [{"source_order": 1, "status": "confirmed", "source_quote": "安全限值",
                   "source_quotes": {"item": "（1）安全限值；"}, "evidence_ids": ["item"], "source_span_ids": ["span-item"]}],
    }
    prepared = _page_evidence({"document_key": "fixture", "evidence_ids": ["item"]}, source, [], [group])["item"]
    profile = ExtractionProfile("construction_standard", "fixture-profile", "standard_or_regulation", ())
    item = {"statement_text": "运行限值和条件包括安全限值。", "statement_type": "fact", "predicate": "describes",
            "subject_entities": [{"surface_form": "运行限值和条件", "role": "subject"}],
            "conditions": [], "applicability_scope": {"status": "unknown"},
            "source_list_item": {"group_id": "classes", "status": "indexed", "item_index": 1, "item_total": 1}}
    candidate = _assemble_candidate(item, prepared, profile, "development_regression_golden", 1)
    assert [binding["support_type"] for binding in candidate["evidence_bindings"]] == ["direct", "context"]
    validate_candidate_against_evidence(candidate, prepared)
    assert summarize_context_groups([group], [candidate], source, {"item"})[0]["status"] == "complete"
    altered = {**candidate, "evidence_bindings": candidate["evidence_bindings"][:1]}
    with pytest.raises(ValueError):
        validate_candidate_against_evidence(altered, prepared)
