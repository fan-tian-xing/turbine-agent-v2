"""The exit gate must account for the full Development Evidence set."""

from copy import deepcopy

from scripts.audit_stage12_exit import _context_group_coverage, _development_evidence_coverage, _operation_group_coverage, _question_group_coverage


def _fixture():
    pages = []
    records = []
    evidence = {}
    outcomes = []
    for number in range(1, 37):
        page_id = f"doc-{number}"
        evidence_id = f"evidence-{number}"
        pages.append({
            "document_logical_id": page_id,
            "revision_id": "revision-1",
            "physical_page": number,
            "evidence_ids": [evidence_id],
            "excluded_evidence_ids": [],
            "coverage_status": "extractable",
        })
        records.append({"split": "development_regression_golden", "document_logical_id": page_id, "physical_page": number})
        evidence[evidence_id] = {
            "review_status": "accepted", "document_logical_id": page_id,
            "revision_id": "revision-1", "locations": [{"physical_page": number}],
        }
        outcomes.append({"evidence_id": evidence_id, "status": "no_statement", "candidate_ids": [], "no_statement_reason": "title only"})
    manifest = {
        "pages": pages,
        "coverage_summary": {"registered_pages": 36, "extractable_evidence": 36, "excluded_evidence": 0, "context_only_evidence": 0, "pages_without_canonical_evidence": 0, "pages_without_extractable_evidence": 0},
    }
    return manifest, {"records": records}, {"candidates": [], "evidence_outcomes": outcomes}, evidence


def test_development_coverage_accepts_all_registered_pages_and_outcomes():
    manifest, registry, candidate, evidence = _fixture()
    candidate["candidates"].append({"candidate_id": "candidate-1", "evidence_bindings": [{"evidence_id": "evidence-1"}]})
    candidate["evidence_outcomes"][0] = {"evidence_id": "evidence-1", "status": "ok", "candidate_ids": ["candidate-1"]}
    assert all(_development_evidence_coverage(manifest, registry, candidate, evidence).values())


def test_development_coverage_rejects_unaccounted_evidence_and_unjustified_empty_result():
    manifest, registry, candidate, evidence = _fixture()
    candidate["evidence_outcomes"].pop()
    assert _development_evidence_coverage(manifest, registry, candidate, evidence)["development_evidence_outcomes_complete"] is False
    candidate["evidence_outcomes"].append({"evidence_id": "evidence-36", "status": "no_statement", "candidate_ids": []})
    assert _development_evidence_coverage(manifest, registry, candidate, evidence)["development_evidence_outcomes_complete"] is False


def test_development_coverage_rejects_missing_registry_page():
    manifest, registry, candidate, evidence = _fixture()
    registry = deepcopy(registry)
    registry["records"].pop()
    assert _development_evidence_coverage(manifest, registry, candidate, evidence)["development_registry_coverage"] is False


def test_unreviewed_ocr_and_context_only_cannot_be_extractable():
    manifest, registry, candidate, evidence = _fixture()
    markers = {"evidence-1": "pending_review", "evidence-2": "context_only"}
    assert _development_evidence_coverage(manifest, registry, candidate, evidence, markers)["development_registry_coverage"] is False
    manifest["pages"][0]["excluded_evidence_ids"] = ["evidence-1"]
    manifest["pages"][0]["evidence_ids"] = []
    manifest["pages"][0]["coverage_status"] = "pending_region_review"
    manifest["pages"][1]["context_only_evidence_ids"] = ["evidence-2"]
    manifest["pages"][1]["context_only_reasons"] = {"evidence-2": "Cross-page fragment is only context."}
    manifest["pages"][1]["evidence_ids"] = []
    manifest["pages"][1]["coverage_status"] = "context_only"
    manifest["coverage_summary"].update(extractable_evidence=34, excluded_evidence=1, context_only_evidence=1, pages_without_extractable_evidence=2)
    candidate["evidence_outcomes"] = candidate["evidence_outcomes"][2:]
    assert all(_development_evidence_coverage(manifest, registry, candidate, evidence, markers).values())
    manifest["pages"][1]["evidence_ids"] = ["evidence-2"]
    assert _development_evidence_coverage(manifest, registry, candidate, evidence, markers)["development_registry_coverage"] is False


def test_question_options_need_same_reviewed_stem_and_options():
    manifest, _, candidate, evidence = _fixture()
    manifest["pages"][0]["document_key"] = "auxiliary_installation_book"
    manifest["pages"][0]["evidence_ids"].extend(["evidence-2", "evidence-3"])
    for number, quote in ((1, "Q1（A）长度"), (2, "A mm B cm"), (3, "Q2 A 不相关")):
        evidence[f"evidence-{number}"].update(
            document_logical_id="doc-1", revision_id="revision-1", locations=[{"physical_page": 1}],
            authority_asset_id="asset-1", source_span_ids=[f"span-{number}"],
            effective_text=quote, source_text_sha256=f"hash-{number}", evidence_version_id=f"version-{number}",
        )
    group = {
        "group_kind": "question_options", "group_id": "question-1", "question_id": "Q1",
        "document_key": "auxiliary_installation_book", "document_logical_id": "doc-1",
        "revision_id": "revision-1", "original_asset_id": "asset-1", "physical_pages": [1],
        "source_title": "Q1", "title_evidence_id": "evidence-1", "stem_evidence_id": "evidence-1",
        "options_evidence_id": "evidence-2", "answer_evidence_id": "evidence-2", "member_evidence_ids": ["evidence-1", "evidence-2"],
        "source_review_status": "confirmed", "answer_marked": True, "answer_option": "A",
        "source_total_items": 2,
        "items": [
            {"source_order": number, "status": "confirmed", "evidence_ids": [f"evidence-{number}"],
             "source_span_ids": [f"span-{number}"], "source_quotes": {f"evidence-{number}": quote}}
            for number, quote in ((1, "Q1（A）长度"), (2, "A mm B cm"))
        ],
    }
    row = {
        "candidate_id": "candidate-1", "statement_text": "长度使用 mm", "statement_type": "fact",
        "source_question": {"group_id": "question-1", "status": "linked"},
        "document_logical_id": "doc-1", "revision_id": "revision-1", "physical_page": 1,
        "source_text_sha256": "hash-1", "evidence_version_id": "version-1",
        "source_span_ids": ["span-1"], "evidence_quote": "Q1（A）长度",
        "evidence_bindings": [
            {"evidence_id": "evidence-1", "support_type": "direct"},
            {"evidence_id": "evidence-2", "support_type": "direct", "document_logical_id": "doc-1",
             "revision_id": "revision-1", "physical_page": 1, "source_text_sha256": "hash-2",
             "evidence_version_id": "version-2", "source_span_ids": ["span-2"], "evidence_quote": "A mm B cm"},
        ],
    }
    candidate["candidates"] = [row]
    candidate["question_group_outcomes"] = [{"group_id": "question-1", "status": "complete", "candidate_ids": ["candidate-1"]}]
    source = {"context_groups": [group]}
    assert all(_question_group_coverage(source, manifest, candidate, evidence).values())
    row["evidence_bindings"][1]["evidence_id"] = "evidence-3"
    assert _question_group_coverage(source, manifest, candidate, evidence)["question_group_pairs_complete"] is False
    row["evidence_bindings"][1]["evidence_id"] = "evidence-2"
    group["source_review_status"] = "unresolved"
    assert _question_group_coverage(source, manifest, candidate, evidence)["question_group_source_confirmed"] is False


def test_operation_group_needs_every_source_step_with_its_own_candidate():
    manifest, _, candidate, evidence = _fixture()
    evidence["evidence-1"].update(source_span_ids=["span-1"], effective_text="先抬起。再清洗。", authority_asset_id="asset-1", source_text_sha256="hash-1", evidence_version_id="version-1")
    source = {"operation_groups": [{
        "group_id": "group-1", "document_key": "D300N", "physical_pages": [1],
        "original_asset_id": "asset-1", "document_logical_id": "doc-1", "revision_id": "revision-1",
        "source_title": "先抬起。", "title_evidence_id": "evidence-1",
        "source_review_status": "confirmed", "source_total_steps": 2,
        "steps": [
            {"source_order": 1, "status": "confirmed", "evidence_id": "evidence-1", "evidence_ids": ["evidence-1"], "source_span_ids": ["span-1"], "source_quote": "先抬起。", "source_quotes": {"evidence-1": "先抬起。"}},
            {"source_order": 2, "status": "confirmed", "evidence_id": "evidence-1", "evidence_ids": ["evidence-1"], "source_span_ids": ["span-1"], "source_quote": "再清洗。", "source_quotes": {"evidence-1": "再清洗。"}},
        ],
    }]}
    manifest["pages"][0]["document_key"] = "D300N"
    for index, quote in ((1, "先抬起。"), (2, "再清洗。")):
        candidate["candidates"].append({
            "candidate_id": f"candidate-{index}", "procedure_group": {"group_id": "group-1", "status": "indexed", "step_index": index, "step_total": 2},
            "evidence_bindings": [{"evidence_id": "evidence-1", "support_type": "direct"}], "source_span_ids": ["span-1"],
            "document_logical_id": "doc-1", "revision_id": "revision-1", "physical_page": 1,
            "source_text_sha256": "hash-1", "evidence_version_id": "version-1", "evidence_quote": quote, "statement_text": quote,
        })
    candidate["operation_group_outcomes"] = [{
        "group_id": "group-1", "status": "complete", "candidate_ids": ["candidate-1", "candidate-2"],
        "covered_step_indices": [1, 2], "missing_step_indices": [],
    }]
    assert all(_operation_group_coverage(source, manifest, candidate, evidence).values())
    candidate["candidates"][0]["evidence_bindings"][0]["support_type"] = "context"
    assert _operation_group_coverage(source, manifest, candidate, evidence)["operation_group_steps_complete"] is False
    candidate["candidates"][0]["evidence_bindings"][0]["support_type"] = "direct"
    evidence["evidence-2"].update(source_span_ids=["span-2"], effective_text="补充步骤上下文。", document_logical_id="doc-1", locations=[{"physical_page": 1}], authority_asset_id="asset-1", source_text_sha256="hash-2", evidence_version_id="version-2")
    manifest["pages"][0]["evidence_ids"].append("evidence-2")
    source["operation_groups"][0]["steps"][0].update(evidence_ids=["evidence-1", "evidence-2"], source_span_ids=["span-1", "span-2"], source_quotes={"evidence-1": "先抬起。", "evidence-2": "补充步骤上下文。"})
    candidate["candidates"][0]["evidence_bindings"].append({"evidence_id": "evidence-2", "support_type": "direct", "document_logical_id": "doc-1", "revision_id": "revision-1", "physical_page": 1, "source_text_sha256": "hash-2", "evidence_version_id": "version-2", "source_span_ids": ["span-2"], "evidence_quote": "补充步骤上下文。"})
    candidate["candidates"][0]["source_span_ids"].append("span-2")
    assert all(_operation_group_coverage(source, manifest, candidate, evidence).values())
    source["operation_groups"][0]["steps"][0].update(evidence_ids=["evidence-1"], source_span_ids=["span-1"], source_quotes={"evidence-1": "先抬起。"})
    candidate["candidates"][0]["evidence_bindings"].pop()
    candidate["candidates"][0]["source_span_ids"].pop()
    candidate["candidates"][0]["evidence_bindings"].append({"evidence_id": "evidence-2", "support_type": "direct"})
    assert _operation_group_coverage(source, manifest, candidate, evidence)["operation_group_steps_complete"] is False
    candidate["candidates"][0]["evidence_bindings"].pop()
    candidate["candidates"].pop()
    assert _operation_group_coverage(source, manifest, candidate, evidence)["operation_group_steps_complete"] is False
    candidate["candidates"].append({"candidate_id": "candidate-2"})
    source["operation_groups"][0]["source_review_status"] = "unresolved"
    assert _operation_group_coverage(source, manifest, candidate, evidence)["operation_group_source_confirmed"] is False


def test_classification_group_covers_each_item_without_procedure_tag():
    manifest, _, candidate, evidence = _fixture()
    manifest["pages"][0]["document_key"] = "HAF103"
    manifest["pages"][0]["evidence_ids"].append("evidence-2")
    manifest["pages"][0]["evidence_ids"].append("evidence-3")
    evidence["evidence-3"].update(
        document_logical_id="doc-1", revision_id="revision-1", locations=[{"physical_page": 1}],
        authority_asset_id="asset-1", source_span_ids=["span-3"], effective_text="职责类别包括以下两类。",
        source_text_sha256="hash-3", evidence_version_id="version-3",
    )
    for number, quote in ((1, "职责类别。"), (2, "方法类别。")):
        evidence[f"evidence-{number}"].update(
            document_logical_id="doc-1", revision_id="revision-1", locations=[{"physical_page": 1}],
            authority_asset_id="asset-1", source_span_ids=[f"span-{number}"],
            effective_text=quote, source_text_sha256=f"hash-{number}", evidence_version_id=f"version-{number}",
        )
        candidate["candidates"].append({
            "candidate_id": f"candidate-{number}",
            "source_list_item": {"group_id": "list-1", "status": "indexed", "item_index": number, "item_total": 2},
            "evidence_bindings": [
                {"evidence_id": f"evidence-{number}", "support_type": "direct"},
                {"evidence_id": "evidence-3", "support_type": "context", "document_logical_id": "doc-1",
                 "revision_id": "revision-1", "source_text_sha256": "hash-3", "evidence_version_id": "version-3",
                 "evidence_quote": "职责类别包括以下两类。"},
            ],
            "source_span_ids": [f"span-{number}"], "document_logical_id": "doc-1",
            "revision_id": "revision-1", "physical_page": 1, "source_text_sha256": f"hash-{number}",
            "evidence_version_id": f"version-{number}", "evidence_quote": quote, "statement_text": quote,
        })
    source = {"context_groups": [{
        "group_kind": "classification_list", "group_id": "list-1", "document_key": "HAF103",
        "document_logical_id": "doc-1", "revision_id": "revision-1", "original_asset_id": "asset-1",
        "physical_pages": [1], "source_title": "职责类别", "title_evidence_id": "evidence-3",
        "member_evidence_ids": ["evidence-1", "evidence-2"], "source_review_status": "confirmed", "source_total_items": 2,
        "items": [
            {"source_order": number, "status": "confirmed", "evidence_id": f"evidence-{number}",
             "evidence_ids": [f"evidence-{number}"], "source_span_ids": [f"span-{number}"],
             "source_quotes": {f"evidence-{number}": quote}}
            for number, quote in ((1, "职责类别。"), (2, "方法类别。"))
        ],
    }]}
    candidate["context_group_outcomes"] = [{
        "group_id": "list-1", "status": "complete", "candidate_ids": ["candidate-1", "candidate-2"],
        "covered_item_indices": [1, 2], "missing_item_indices": [],
    }]
    assert all(_context_group_coverage(source, manifest, candidate, evidence).values())
    candidate["candidates"][1]["procedure_group"] = {"group_id": "list-1", "status": "indexed", "step_index": 2, "step_total": 2}
    assert _context_group_coverage(source, manifest, candidate, evidence)["context_group_items_complete"] is False
