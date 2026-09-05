"""Build the metadata-only Source Registry from controlled inputs."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .duplicates import duplicate_relations, materialize_duplicate_groups
from .identity import asset_id_for_path, load_document_identity_map
from .inspection import inspect_pdf
from .io import jsonl_write, load_manual_findings
from .profiles import default_text_adapter_status, load_source_profiles, profile_for_path
from .schema import (
    aggregate_document_status,
    collapsed_context,
    collapsed_relation_status,
    validate_asset_record,
    validate_document_record,
)
from .source_inputs import load_allowlist


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE_ROOT = PROJECT_ROOT.parent / "Original materials"
ALLOWLIST_PATH = PROJECT_ROOT / "config" / "source_allowlist.tsv"
REGISTRY_ROOT = PROJECT_ROOT / "data" / "registry"
MANUAL_FINDINGS_PATH = REGISTRY_ROOT / "source_manual_findings.jsonl"
DOCUMENT_IDENTITY_PATH = PROJECT_ROOT / "config" / "document_identity.tsv"


def build_registry(source_root: Path) -> dict[str, int]:
    metadata, entries = load_allowlist(ALLOWLIST_PATH)
    if int(metadata.get("expected_count", "-1")) != len(entries):
        raise ValueError("allowlist expected_count does not match its rows")
    profiles, profile_assignments, default_profile_id = load_source_profiles()
    document_identity = load_document_identity_map(DOCUMENT_IDENTITY_PATH)
    allowlisted_paths = {entry["path"] for entry in entries}
    if set(document_identity) != allowlisted_paths:
        missing = sorted(allowlisted_paths - set(document_identity))
        extra = sorted(set(document_identity) - allowlisted_paths)
        details = []
        if missing:
            details.append(f"missing paths: {missing}")
        if extra:
            details.append(f"unallowlisted paths: {extra}")
        raise ValueError("document identity map does not match source allowlist (" + "; ".join(details) + ")")
    source_root = source_root.resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(f"SOURCE_ROOT does not exist: {source_root}")

    inspected: list[dict[str, Any]] = []
    for entry in entries:
        relative_path = entry["path"]
        path = (source_root / Path(*relative_path.split("/"))).resolve()
        if not path.is_file() or not path.is_relative_to(source_root):
            raise FileNotFoundError(f"allowlisted source is not a file inside SOURCE_ROOT: {relative_path}")
        profile = profile_for_path(relative_path, profiles, profile_assignments, default_profile_id)
        if profile.profile_id == default_profile_id:
            raise ValueError(f"allowlisted source has no explicit source profile: {relative_path}")
        inspected.append(
            inspect_pdf(
                path,
                relative_path,
                int(entry["size_bytes"]),
                entry["sha256"],
            )
        )

    groups, relations = duplicate_relations(inspected)
    path_to_index = {asset["relative_path"]: index for index, asset in enumerate(inspected)}
    by_document: dict[str, list[str]] = defaultdict(list)
    for relative_path, document_id in document_identity.items():
        by_document[document_id].append(relative_path)
    for document_paths in by_document.values():
        originals = [
            path for path in document_paths
            if profile_for_path(path, profiles, profile_assignments, default_profile_id).source_role != "derived_ocr_asset"
        ]
        derivatives = [
            path for path in document_paths
            if profile_for_path(path, profiles, profile_assignments, default_profile_id).source_role == "derived_ocr_asset"
        ]
        if not originals:
            continue
        for derivative in derivatives:
            source = originals[0]
            groups.union(path_to_index[source], path_to_index[derivative])
            relations.append(
                {
                    "relation_type": "ocr_derivative_of",
                    "asset_paths": [source, derivative],
                    "comparison_basis": "controlled logical-document identity map and derived_ocr_asset profile",
                }
            )

    manual_findings = load_manual_findings(MANUAL_FINDINGS_PATH)
    relation_paths: dict[str, list[str]] = defaultdict(list)
    for relation in relations:
        for relation_path in relation["asset_paths"]:
            relation_paths[relation_path].append(relation["relation_type"])

    asset_records: list[dict[str, Any]] = []
    review_records: list[dict[str, Any]] = []
    document_members: dict[str, list[str]] = defaultdict(list)
    for inspected_asset in inspected:
        path = inspected_asset["relative_path"]
        profile = profile_for_path(path, profiles, profile_assignments, default_profile_id)
        relation_types = sorted(set(relation_paths.get(path, [])))
        role = profile.source_role
        admission_status = (
            "reference_only" if role == "standards_catalog"
            else "duplicate_or_derivative" if role == "derived_ocr_asset"
            else "metadata_review_required"
        )
        manual = manual_findings.get(path, {})
        if manual.get("admission_status"):
            admission_status = manual["admission_status"]
        identity_status = manual.get("identity_status", inspected_asset["identity_status"])
        completeness_status = manual.get("completeness_status", inspected_asset["completeness_status"])
        applicability_status = manual.get("applicability_status", "review_required")
        applicability_scope = manual.get("applicability_scope", ["unknown"])
        if not isinstance(applicability_scope, list) or not applicability_scope or not all(
            isinstance(item, str) and item.strip() for item in applicability_scope
        ):
            raise ValueError(f"applicability_scope must be a non-empty array of strings for {path}")
        title_candidate = manual.get("title_candidate", inspected_asset["title_candidate"])
        title_source = manual.get("title_source", inspected_asset["title_source"])
        identifier_candidates = manual.get("identifier_candidates", inspected_asset["identifier_candidates"])
        year_candidates = manual.get("year_candidates", inspected_asset["year_candidates"])
        pdf_metadata = manual.get("pdf_metadata", inspected_asset["pdf_metadata"])
        if not isinstance(title_candidate, str) or not isinstance(title_source, str):
            raise ValueError(f"title metadata must be strings for {path}")
        if not isinstance(identifier_candidates, list) or not all(isinstance(item, str) for item in identifier_candidates):
            raise ValueError(f"identifier_candidates must be an array of strings for {path}")
        if not isinstance(year_candidates, list) or not all(isinstance(item, str) for item in year_candidates):
            raise ValueError(f"year_candidates must be an array of strings for {path}")
        if not isinstance(pdf_metadata, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in pdf_metadata.items()):
            raise ValueError(f"pdf_metadata must be a string map for {path}")
        text_adapter_status = manual.get(
            "text_adapter_status",
            default_text_adapter_status(profile, inspected_asset["text_layer_status"]),
        )
        authority_level = manual.get("authority_level", "unknown")
        normative_modality = manual.get("normative_modality", "unknown")
        project_adoption_status = manual.get("project_adoption_status", "unknown")
        manufacturer_approval_status = manual.get("manufacturer_approval_status", "unknown")
        validity_status = manual.get("validity_status", "unknown")
        external_processing_status = manual.get("external_processing_status", inspected_asset["external_processing_status"])
        supersedes = manual.get("supersedes", [])
        supersedes_status = manual.get("supersedes_status", "review_required")
        exception_basis = manual.get("exception_basis", [])
        exception_basis_status = manual.get("exception_basis_status", "review_required")
        flags = sorted(set(inspected_asset["review_flags"] + relation_types + manual.get("review_flags", [])))
        asset_id = asset_id_for_path(path)
        document_id = document_identity[path]
        document_members[document_id].append(asset_id)
        asset_record = {
            "asset_id": asset_id,
            "document_logical_id": document_id,
            "revision_id": f"sha256:{inspected_asset['sha256']}",
            "source_profile_id": profile.profile_id,
            "relative_path": path,
            "file_name": inspected_asset["file_name"],
            "size_bytes": inspected_asset["size_bytes"],
            "sha256": inspected_asset["sha256"],
            "page_count": inspected_asset["page_count"],
            "source_role": role,
            "text_layer_status": inspected_asset["text_layer_status"],
            "text_adapter_status": text_adapter_status,
            "text_character_count": inspected_asset["total_text_characters"],
            "rotated_page_count": inspected_asset["rotated_page_count"],
            "title_candidate": title_candidate,
            "title_source": title_source,
            "identifier_candidates": identifier_candidates,
            "year_candidates": year_candidates,
            "identity_status": identity_status,
            "completeness_status": completeness_status,
            "external_processing_status": external_processing_status,
            "authority_level": authority_level,
            "normative_modality": normative_modality,
            "project_adoption_status": project_adoption_status,
            "manufacturer_approval_status": manufacturer_approval_status,
            "validity_status": validity_status,
            "supersedes": supersedes,
            "supersedes_status": supersedes_status,
            "exception_basis": exception_basis,
            "exception_basis_status": exception_basis_status,
            "visual_content_fingerprint": inspected_asset["visual_content_fingerprint"],
            "text_content_fingerprint": inspected_asset["text_content_fingerprint"],
            "pdf_metadata": pdf_metadata,
            "admission_status": admission_status,
            "applicability_status": applicability_status,
            "applicability_scope": applicability_scope,
            "review_flags": flags,
            "manual_findings": manual.get("findings", []),
            "manual_notes": manual.get("notes", []),
            **({"manual_follow_up": manual["follow_up_required"]} if manual.get("follow_up_required") else {}),
        }
        validate_asset_record(asset_record)
        asset_records.append(asset_record)
        confirmed_flags = set(manual.get("resolved_flags", []))
        queue_flags = list(flags)
        if admission_status in {"metadata_review_required", "quarantined"}:
            if identity_status != "confirmed":
                queue_flags.append("identity_review_required")
            if completeness_status == "review_required":
                queue_flags.append("completeness_review_required")
            if applicability_status != "confirmed":
                queue_flags.append("applicability_review_required")
            if applicability_scope == ["unknown"]:
                queue_flags.append("applicability_scope_review_required")
            if external_processing_status == "not_assessed":
                queue_flags.append("external_processing_review_required")
            if any(value in {"unknown", "review_required"} for value in (
                authority_level, normative_modality, project_adoption_status,
                manufacturer_approval_status, validity_status, supersedes_status, exception_basis_status,
            )):
                queue_flags.append("normative_context_review_required")
            if text_adapter_status not in {"native_text_available", "ocr_validated"}:
                queue_flags.append("text_adapter_review_required")
        for flag in sorted(set(queue_flags)):
            review_records.append(
                {
                    "review_id": f"review-{asset_id_for_path(asset_id + '|' + flag)[6:]}",
                    "asset_id": asset_id,
                    "relative_path": path,
                    "issue_type": flag,
                    "status": "resolved" if flag in confirmed_flags else "open",
                    "question": f"Confirm {flag.replace('_', ' ')} before semantic processing.",
                    **({"resolution": "Resolved by checked-in manual finding; source PDF remains unchanged."} if flag in confirmed_flags else {}),
                }
            )
        if manual.get("follow_up_required"):
            review_records.append(
                {
                    "review_id": f"review-{asset_id_for_path(asset_id + '|manual_follow_up')[6:]}",
                    "asset_id": asset_id,
                    "relative_path": path,
                    "issue_type": "manual_follow_up",
                    "status": "open",
                    "question": manual["follow_up_required"],
                }
            )

    open_review_counts: dict[str, int] = defaultdict(int)
    for item in review_records:
        if item["status"] == "open":
            open_review_counts[item["asset_id"]] += 1
    asset_records_by_id = {record["asset_id"]: record for record in asset_records}
    asset_records_by_path = {record["relative_path"]: record for record in asset_records}
    document_records: list[dict[str, Any]] = []
    for document_id, member_assets in sorted(document_members.items()):
        member_records = [asset_records_by_id[asset_id] for asset_id in member_assets]
        eligible = [record for record in member_records if record["admission_status"] not in {"duplicate_or_derivative", "reference_only"}]
        canonical = next((record for record in eligible if record["admission_status"] == "admitted"), member_records[0])
        adapter = next((record["text_adapter_status"] for record in member_records if record["text_adapter_status"] in {"native_text_available", "ocr_validated"}), "text_review_required")
        document_record = {
            "document_logical_id": document_id,
            "asset_ids": member_assets,
            "canonical_asset_id": canonical["asset_id"],
            "title_candidates": sorted({record["title_candidate"] for record in member_records}),
            "source_roles": sorted({record["source_role"] for record in member_records}),
            "document_status": aggregate_document_status(member_records),
            "identity_status": "confirmed" if eligible and all(record["identity_status"] == "confirmed" for record in eligible) else "review_required",
            "completeness_status": "complete" if eligible and all(record["completeness_status"] == "complete" for record in eligible) else "review_required",
            "applicability_status": "confirmed" if eligible and all(record["applicability_status"] == "confirmed" for record in eligible) else "review_required",
            "applicability_scope": sorted({scope for record in member_records for scope in record["applicability_scope"] if scope != "unknown"}) or ["unknown"],
            "text_adapter_status": adapter,
            "authority_level": collapsed_context(member_records, "authority_level"),
            "normative_modality": collapsed_context(member_records, "normative_modality"),
            "project_adoption_status": collapsed_context(member_records, "project_adoption_status"),
            "manufacturer_approval_status": collapsed_context(member_records, "manufacturer_approval_status"),
            "validity_status": collapsed_context(member_records, "validity_status"),
            "supersedes": sorted({item for record in member_records for item in record["supersedes"]}),
            "supersedes_status": collapsed_relation_status(member_records, "supersedes_status"),
            "exception_basis": sorted({item for record in member_records for item in record["exception_basis"]}),
            "exception_basis_status": collapsed_relation_status(member_records, "exception_basis_status"),
            "open_review_count": sum(open_review_counts[record["asset_id"]] for record in member_records),
            "member_relation_flags": sorted({flag for record in member_records for flag in record["review_flags"]}),
        }
        validate_document_record(document_record)
        document_records.append(document_record)

    group_records, relation_records = materialize_duplicate_groups(
        groups, inspected, relations, asset_records_by_path, manual_findings
    )
    REGISTRY_ROOT.mkdir(parents=True, exist_ok=True)
    jsonl_write(REGISTRY_ROOT / "source_assets.jsonl", asset_records)
    jsonl_write(REGISTRY_ROOT / "source_documents.jsonl", document_records)
    jsonl_write(REGISTRY_ROOT / "source_duplicate_groups.jsonl", group_records)
    jsonl_write(REGISTRY_ROOT / "source_duplicate_relations.jsonl", relation_records)
    jsonl_write(REGISTRY_ROOT / "source_review_queue.jsonl", review_records)
    summary = {
        "schema_version": 1,
        "source_root": "../Original materials",
        "physical_asset_count": len(asset_records),
        "logical_document_count": len(document_records),
        "admitted_source_count": sum(record["admission_status"] == "admitted" for record in asset_records),
        "independent_extractable_source_count": sum(
            document["document_status"] == "admitted" and document["text_adapter_status"] in {"native_text_available", "ocr_validated"}
            for document in document_records
        ),
        "duplicate_group_count": len(group_records),
        "duplicate_relation_count": len(relation_records),
        "review_queue_count": len(review_records),
        "manual_finding_count": sum(bool(record["manual_findings"]) for record in asset_records),
        "admission_status_counts": {
            status: sum(record["admission_status"] == status for record in asset_records)
            for status in {"admitted", "quarantined", "metadata_review_required", "duplicate_or_derivative", "reference_only", "excluded_private_evaluation"}
        },
        "text_layer_status_counts": {
            status: sum(record["text_layer_status"] == status for record in asset_records)
            for status in {"native_text", "mixed_or_low_quality", "scan_only"}
        },
        "text_adapter_status_counts": {
            status: sum(record["text_adapter_status"] == status for record in asset_records)
            for status in sorted({"not_assessed", "native_text_available", "ocr_required", "ocr_pending_validation", "ocr_validated", "text_review_required"})
        },
        "source_role_counts": {
            role: sum(record["source_role"] == role for record in asset_records)
            for role in sorted({record["source_role"] for record in asset_records})
        },
        "processing_note": "Metadata and fingerprints only; no source text copied and no Neo4j write performed.",
    }
    (REGISTRY_ROOT / "source_registry_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "physical_assets": len(asset_records),
        "logical_documents": len(document_records),
        "duplicate_groups": len(group_records),
        "duplicate_relations": len(relation_records),
        "review_items": len(review_records),
    }
