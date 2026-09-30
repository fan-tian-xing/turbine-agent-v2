"""Check whole-page Stage 6 semantic coverage before Stage 12 development use.

Existing Stage 6 quality checks establish the integrity of selected Evidence.
This separate gate requires an explicit original-page completeness decision.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from turbine_kg.settings import Settings

from build_stage6_golden_evidence import DOCUMENTS
from stage6_cross_page_supplements import load_supplements as load_cross_page_supplements


ROOT = Path(__file__).resolve().parents[1]
STAGE6 = ROOT / "data" / "stage6"
GOLDEN = STAGE6 / "stage6_evidence_golden_sample.json"
BUNDLE = STAGE6 / "stage6_evidence_bundle.jsonl"
DECISIONS = STAGE6 / "stage6_semantic_coverage_decisions.jsonl"
STRUCTURE = STAGE6 / "stage6_source_structure_index.json"
OVERRIDES = STAGE6 / "stage6_source_review_overrides.json"
REGISTRY_ASSETS = ROOT / "data" / "registry" / "source_assets.jsonl"
REGISTRY_FINDINGS = ROOT / "data" / "registry" / "source_manual_findings.jsonl"
OUTPUT = STAGE6 / "stage6_semantic_coverage_audit.json"
NEGATIVE_ELIGIBILITY = {"metadata_only", "navigation_only", "boundary_only"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _page_fingerprint(rows: list[dict]) -> str:
    return hashlib.sha256(
        "\x1f".join(row["evidence"]["evidence_id"] for row in rows).encode("utf-8")
    ).hexdigest()


def _validate_structure(index: dict, evidence_by_id: dict[str, dict]) -> list[str]:
    errors: list[str] = []
    for name, path in (("evidence_bundle", BUNDLE), ("golden_sample", GOLDEN)):
        source = index.get("inputs", {}).get(name, {})
        if source.get("path") != path.relative_to(ROOT).as_posix() or source.get("sha256") != _sha(path):
            errors.append(f"structure_input_stale:{name}")
    ids: set[str] = set()
    for group in index.get("operation_groups", []):
        group_id = group.get("group_id")
        if not group_id or group_id in ids:
            errors.append(f"duplicate_or_missing_group_id:{group_id}")
        ids.add(group_id)
        steps = group.get("steps", [])
        if [step.get("source_order") for step in steps] != list(range(1, len(steps) + 1)):
            errors.append(f"noncontiguous_source_order:{group_id}")
        if group.get("source_review_status") == "unresolved":
            continue
        if group.get("source_review_status") != "confirmed":
            errors.append(f"invalid_group_status:{group_id}")
            continue
        if not steps or group.get("source_total_steps") != len(steps):
            errors.append(f"incomplete_step_count:{group_id}")
        title = evidence_by_id.get(group.get("title_evidence_id"))
        if not title or group.get("source_title", "") not in title["evidence"]["effective_text"]:
            errors.append(f"unbound_source_title:{group_id}")
        elif any(
            title["evidence"].get(field) != group.get(field)
            for field in ("document_logical_id", "revision_id")
        ):
            errors.append(f"title_identity_mismatch:{group_id}")
        for step in steps:
            member_ids = step.get("evidence_ids", [])
            members = [evidence_by_id.get(evidence_id) for evidence_id in member_ids]
            quotes = step.get("source_quotes", {})
            if (
                step.get("status") != "confirmed"
                or not member_ids
                or len(member_ids) != len(set(member_ids))
                or any(member is None for member in members)
                or set(quotes) != set(member_ids)
                or (len(member_ids) == 1 and step.get("evidence_id") != member_ids[0])
            ):
                errors.append(f"unbound_step:{group_id}:{step.get('source_order')}")
                continue
            values = [member["evidence"] for member in members]
            all_span_ids = {span for value in values for span in value["source_span_ids"]}
            if (
                not step.get("source_quote")
                or not step.get("source_span_ids")
                or not set(step["source_span_ids"]) <= all_span_ids
                or any(
                    value.get("document_logical_id") != group.get("document_logical_id")
                    or value.get("revision_id") != group.get("revision_id")
                    or value.get("authority_asset_id") != group.get("original_asset_id")
                    or member["input"]["physical_page"] not in group.get("physical_pages", [])
                    or not quotes[evidence_id]
                    or quotes[evidence_id] not in value["effective_text"]
                    for evidence_id, member, value in zip(member_ids, members, values)
                )
            ):
                errors.append(f"step_source_mismatch:{group_id}:{step.get('source_order')}")
    for group in index.get("context_groups", []):
        group_id = group.get("group_id")
        if not group_id or group_id in ids:
            errors.append(f"duplicate_or_missing_group_id:{group_id}")
        ids.add(group_id)
        items = group.get("items", [])
        kind = group.get("group_kind")
        if kind not in {"classification_list", "requirement_list", "activity_list", "question_options", "question_answer"}:
            errors.append(f"invalid_context_group_kind:{group_id}")
        if group.get("source_review_status") != "confirmed" or group.get("source_total_items") != len(items):
            errors.append(f"incomplete_context_group:{group_id}")
        if [item.get("source_order") for item in items] != list(range(1, len(items) + 1)):
            errors.append(f"noncontiguous_context_order:{group_id}")
        title = evidence_by_id.get(group.get("title_evidence_id"))
        if not title or group.get("source_title", "") not in title["evidence"]["effective_text"]:
            errors.append(f"unbound_context_title:{group_id}")
        elif any(
            title["evidence"].get(field) != group.get(field)
            for field in ("document_logical_id", "revision_id")
        ):
            errors.append(f"context_title_identity_mismatch:{group_id}")
        member_ids = group.get("member_evidence_ids", [])
        if set(member_ids) != {item_id for item in items for item_id in item.get("evidence_ids", [])}:
            errors.append(f"context_members_mismatch:{group_id}")
        if kind == "question_options":
            stem_id = group.get("stem_evidence_id")
            options_id = group.get("options_evidence_id")
            split_pair = (
                len(items) == 2
                and [item.get("source_label") for item in items] == ["stem", "options"]
                and stem_id != options_id
                and set(member_ids) == {stem_id, options_id}
                and items[0].get("evidence_ids") == [stem_id]
                and items[1].get("evidence_ids") == [options_id]
            )
            combined_pair = (
                len(items) == 1
                and items[0].get("source_label") == "stem_and_options"
                and stem_id == options_id
                and member_ids == [stem_id]
                and items[0].get("evidence_ids") == [stem_id]
            )
            if (
                not stem_id or not options_id or not (split_pair or combined_pair)
                or group.get("title_evidence_id") != stem_id
                or group.get("source_title") != group.get("question_id")
                or group.get("answer_marked") is not True
                or group.get("answer_option") not in {"A", "B", "C", "D"}
                or group.get("answer_evidence_id") != options_id
                or not group.get("answer_text")
                or group["answer_text"] not in group.get("answer_source_quote", "")
                or not re.match(
                    rf"^[（(]{group['answer_option']}[）)]",
                    group.get("answer_source_quote", ""),
                )
                or group.get("answer_source_quote", "") not in evidence_by_id.get(options_id, {}).get("evidence", {}).get("effective_text", "")
                or group.get("question_id", "") not in evidence_by_id.get(stem_id, {}).get("evidence", {}).get("effective_text", "")
            ):
                errors.append(f"invalid_question_options_group:{group_id}")
        if kind == "question_answer":
            question_id = group.get("question_evidence_id")
            answer_ids = group.get("answer_evidence_ids", [])
            question = evidence_by_id.get(question_id)
            answers = [evidence_by_id.get(evidence_id) for evidence_id in answer_ids]
            if not (
                question_id == group.get("title_evidence_id")
                and question and question.get("stage12_extractability") == "context_only"
                and group.get("question_id") in question["evidence"]["effective_text"]
                and answer_ids and len(answer_ids) == len(set(answer_ids))
                and set(member_ids) == {question_id, *answer_ids}
                and [item.get("evidence_ids") for item in items] == [[question_id], *[[eid] for eid in answer_ids]]
                and all(answer and answer.get("stage12_extractability") == "extractable"
                        and answer["evidence"].get("evidence_role") == "approved_answer"
                        and answer["evidence"].get("review_status") == "accepted"
                        for answer in answers)
                and group.get("authority_kind") and group.get("next_source_boundary")
            ):
                errors.append(f"invalid_question_answer_group:{group_id}")
        for item in items:
            item_ids = item.get("evidence_ids", [])
            item_members = [evidence_by_id.get(item_id) for item_id in item_ids]
            quotes = item.get("source_quotes", {})
            if (
                item.get("status") != "confirmed"
                or not item_ids
                or any(member is None for member in item_members)
                or set(quotes) != set(item_ids)
            ):
                errors.append(f"unbound_context_item:{group_id}:{item.get('source_order')}")
                continue
            values = [member["evidence"] for member in item_members]
            all_spans = {span for value in values for span in value["source_span_ids"]}
            if (
                not item.get("source_span_ids")
                or not set(item["source_span_ids"]) <= all_spans
                or any(
                    value.get("document_logical_id") != group.get("document_logical_id")
                    or value.get("revision_id") != group.get("revision_id")
                    or value.get("authority_asset_id") != group.get("original_asset_id")
                    or member["input"]["physical_page"] not in group.get("physical_pages", [])
                    or not quotes[item_id]
                    or quotes[item_id] not in value["effective_text"]
                    for item_id, member, value in zip(item_ids, item_members, values)
                )
            ):
                errors.append(f"context_item_source_mismatch:{group_id}:{item.get('source_order')}")
    return errors


def build_audit() -> dict:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    bundle = _jsonl(BUNDLE)
    decisions = _jsonl(DECISIONS)
    structure = json.loads(STRUCTURE.read_text(encoding="utf-8"))
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    source_asset = next(
        row for row in _jsonl(REGISTRY_ASSETS)
        if row.get("asset_kind") == "original"
        and row.get("relative_path") == DOCUMENTS["DLT863"]["original"]
    )
    source_finding = next(
        row for row in _jsonl(REGISTRY_FINDINGS)
        if row.get("relative_path") == DOCUMENTS["DLT863"]["original"]
        and row.get("finding_id") == "manual-dlt863-page-review"
    )
    missing_printed_pages = source_finding.get("missing_printed_body_pages", [])
    settings = Settings.from_environment()
    pdf_hashes = {
        key: _sha(settings.source_root / document["original"])
        for key, document in DOCUMENTS.items()
    }
    by_page: dict[tuple[str, int], list[dict]] = defaultdict(list)
    by_id: dict[str, dict] = {}
    for row in bundle:
        key = (row["document_key"], int(row["input"]["physical_page"]))
        by_page[key].append(row)
        by_id[row["evidence"]["evidence_id"]] = row
    decisions_by_page = {(row["document_key"], int(row["physical_page"])): row for row in decisions}
    sample_pages = {(row["document_key"], int(row["physical_page"])) for row in golden["records"]}
    errors: list[str] = []
    if len(decisions_by_page) != len(decisions) or not set(decisions_by_page) <= sample_pages:
        errors.append("coverage_decisions_duplicate_or_out_of_scope")
    if len(by_id) != len(bundle):
        errors.append("canonical_evidence_ids_not_unique")
    if (
        source_asset.get("completeness_status") != "incomplete"
        or len(missing_printed_pages) != 15
        or source_finding.get("missing_printed_body_page_count") != len(missing_printed_pages)
    ):
        errors.append("dlt863_source_missing_page_boundary_stale")
    errors.extend(_validate_structure(structure, by_id))
    incomplete_source_by_page: dict[tuple[str, int], int] = defaultdict(int)
    for row in overrides["source_fragment_exclusions"]:
        if row.get("kind") == "excluded_incomplete_source":
            incomplete_source_by_page[(row["document_key"], int(row["physical_page"]))] += 1
    for row in overrides["omitted_incomplete_source_regions"]:
        if row.get("status") != "excluded_incomplete_source" or not row.get("reason"):
            errors.append(f"invalid_omitted_source_region:{row.get('region_id')}")
        incomplete_source_by_page[(row["document_key"], int(row["physical_page"]))] += 1
    cross_units = {
        unit["source_unit_id"]: unit
        for unit in load_cross_page_supplements()["source_units"]
    }
    restored_cross_pages: set[tuple[str, int]] = set()
    for row in bundle:
        unit = cross_units.get(row.get("cross_page_source_unit_id"))
        if not unit:
            continue
        actual_pages = {int(location["physical_page"]) for location in row["evidence"]["locations"]}
        if (
            row["document_key"] != unit["document_key"]
            or actual_pages != set(unit["physical_pages"])
            or row["evidence"]["review_status"] != "accepted"
        ):
            errors.append(f"cross_page_source_not_bound:{unit['source_unit_id']}")
            continue
        restored_cross_pages.update((unit["document_key"], page) for page in actual_pages)
    paired_marked_questions = {
        (group.get("document_key"), page, group.get("question_id"))
        for group in structure.get("context_groups", [])
        if group.get("group_kind") == "question_options"
        and group.get("source_review_status") == "confirmed"
        and group.get("answer_marked") is True
        for page in group.get("physical_pages", [])
    }

    extractability = defaultdict(int)
    for row in bundle:
        status = row.get("stage12_extractability", "extractable")
        extractability[status] += 1
        if status not in {"extractable", "context_only", "pending_review"}:
            errors.append(f"invalid_extractability:{row['evidence']['evidence_id']}")
        if status != "extractable" and not row.get("extractability_reason"):
            errors.append(f"missing_extractability_reason:{row['evidence']['evidence_id']}")

    page_results = []
    for sample in golden["records"]:
        key = (sample["document_key"], int(sample["physical_page"]))
        decision = decisions_by_page.get(key)
        eligibility = sample["evidence_eligibility"]
        if eligibility in NEGATIVE_ELIGIBILITY:
            status = "excluded_nonsemantic"
            reason = "Golden Sample original-page negative-gate decision"
            if by_page[key] or decision:
                errors.append(f"negative_page_promoted_or_reviewed:{key}")
        else:
            status = decision["decision"] if decision else "unresolved"
            reason = decision["reason"] if decision else (
                "Only table-region Evidence exists; individual cells and surrounding prose have not been reconciled with the original page."
                if eligibility == "quarantined" else
                "Selected Evidence is accepted, but whole-page original-to-Evidence semantic coverage has not been reviewed."
            )
            if decision:
                if decision.get("review_scope") != "whole_original_page":
                    errors.append(f"incomplete_review_scope:{key}")
                if decision.get("original_pdf_sha256") != pdf_hashes[sample["document_key"]]:
                    errors.append(f"source_pdf_changed_since_review:{key}")
                if status == "complete" and decision.get("expected_page_fingerprint") != _page_fingerprint(by_page[key]):
                    errors.append(f"evidence_changed_since_coverage_review:{key}")
            if status not in {"complete", "unresolved", "confirmed_gap"}:
                errors.append(f"invalid_coverage_decision:{key}")
            if (
                status == "complete" and incomplete_source_by_page[key]
                and key not in restored_cross_pages
                and decision.get("incomplete_source_exclusion_reviewed") is not True
            ):
                errors.append(f"complete_page_has_unreviewed_source_exclusion:{key}")
            if not by_page[key]:
                errors.append(f"positive_page_without_evidence:{key}")
            if status == "complete":
                for row in by_page[key]:
                    text = row["evidence"]["effective_text"]
                    for match in re.finditer(r"\b([A-Z][a-z]\d[A-Z]\d{4})\b.{0,160}?[（(][ABCD][）)]", text, re.DOTALL):
                        if (key[0], key[1], match.group(1)) not in paired_marked_questions:
                            errors.append(f"complete_page_unpaired_marked_question:{key}:{match.group(1)}")
        page_results.append({
            "document_key": key[0],
            "physical_page": key[1],
            "eligibility": eligibility,
            "evidence_count": len(by_page[key]),
            "excluded_incomplete_source_count": incomplete_source_by_page[key],
            "status": status,
            "reason": reason,
        })

    unresolved = sum(row["status"] == "unresolved" for row in page_results)
    gaps = sum(row["status"] == "confirmed_gap" for row in page_results)
    unresolved_groups = sum(
        group.get("source_review_status") != "confirmed"
        for group in structure.get("operation_groups", [])
    ) + sum(
        group.get("source_review_status") != "confirmed"
        for group in structure.get("context_groups", [])
    )
    ready = not errors and unresolved == 0 and gaps == 0 and unresolved_groups == 0 and extractability["pending_review"] == 0
    return {
        "schema_version": 1,
        "stage": "6",
        "artifact_kind": "stage6_semantic_coverage_audit",
        "coverage_scope": "sampled_existing_original_pdf_pages_only; source-document missing pages are not reconstructed",
        "status": "complete" if ready else "blocked",
        "stage12_input_allowed": ready,
        "inputs": {
            "golden_sample": {"path": GOLDEN.relative_to(ROOT).as_posix(), "sha256": _sha(GOLDEN)},
            "evidence_bundle": {"path": BUNDLE.relative_to(ROOT).as_posix(), "sha256": _sha(BUNDLE)},
            "coverage_decisions": {"path": DECISIONS.relative_to(ROOT).as_posix(), "sha256": _sha(DECISIONS)},
            "source_structure_index": {"path": STRUCTURE.relative_to(ROOT).as_posix(), "sha256": _sha(STRUCTURE)},
            "source_review_overrides": {"path": OVERRIDES.relative_to(ROOT).as_posix(), "sha256": _sha(OVERRIDES)},
            "source_assets": {"path": REGISTRY_ASSETS.relative_to(ROOT).as_posix(), "sha256": _sha(REGISTRY_ASSETS)},
            "source_manual_findings": {"path": REGISTRY_FINDINGS.relative_to(ROOT).as_posix(), "sha256": _sha(REGISTRY_FINDINGS)},
        },
        "original_pdf_sha256": pdf_hashes,
        "source_document_completeness": {
            "DLT863": {
                "status": "incomplete",
                "missing_printed_body_pages": missing_printed_pages,
                "missing_printed_body_page_count": len(missing_printed_pages),
            }
        },
        "page_count": len(page_results),
        "complete_page_count": sum(row["status"] == "complete" for row in page_results),
        "excluded_nonsemantic_page_count": sum(row["status"] == "excluded_nonsemantic" for row in page_results),
        "unresolved_page_count": unresolved,
        "confirmed_gap_page_count": gaps,
        "excluded_incomplete_source_count": sum(incomplete_source_by_page.values()),
        "unresolved_operation_group_count": unresolved_groups,
        "extractability_counts": dict(extractability),
        "page_results": page_results,
        "errors": errors,
    }


def main() -> None:
    audit = build_audit()
    OUTPUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": audit["status"],
        "pages": audit["page_count"],
        "unresolved": audit["unresolved_page_count"],
        "confirmed_gaps": audit["confirmed_gap_page_count"],
        "errors": audit["errors"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
