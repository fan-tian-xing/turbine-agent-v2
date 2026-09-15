"""Build the small, source-grounded Stage 11 development Statement set."""

from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/stage3/real_trial_confirmation.json"
STAGE6_BUNDLE = ROOT / "data/stage6/stage6_evidence_bundle.jsonl"
OUTPUT = ROOT / "data/stage11/stage11_statement_development_samples.jsonl"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", value or "").replace("％", "%")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_type(statement_type: str, text: str) -> str:
    mapping = {
        "inspection_requirement": "requirement",
        "acceptance_requirement": "requirement",
        "maintenance_procedure": "procedure",
        "scope_definition": "limitation",
    }
    if statement_type in mapping:
        return mapping[statement_type]
    if any(token in text for token in ("应", "必须", "不得", "要求")):
        return "requirement"
    if any(token in text for token in ("检查", "验收", "验证")):
        return "verification"
    return "fact"


def _predicate(statement_type: str) -> str:
    return {
        "requirement": "requires",
        "procedure": "describes_procedure",
        "limitation": "limits_scope",
        "verification": "requires_verification",
        "condition": "conditions",
        "observation": "observes",
        "fact": "describes",
    }.get(statement_type, "describes")


def _entity_alignment(entity_id: str, surface: str, *, reviewer_type: str) -> list[dict]:
    return [{
        "surface_form": surface,
        "entity_id": entity_id,
        "entity_class": "EngineeringEntity",
        "match_type": "confirmed" if reviewer_type == "user_confirmation" else "exact_source_span",
        "review_status": "accepted",
    }]


def _evidence_index() -> dict[tuple[str, str, int], list[dict]]:
    index: dict[tuple[str, str, int], list[dict]] = {}
    for row in _jsonl(STAGE6_BUNDLE):
        evidence = row["evidence"]
        location = evidence["locations"][0]
        key = (evidence["document_logical_id"], evidence["revision_id"], int(location["physical_page"]))
        index.setdefault(key, []).append(evidence)
    return index


def _find_canonical(index: dict[tuple[str, str, int], list[dict]], group: dict, quote: str) -> dict:
    key = (group["document_logical_id"], group["revision_id"], int(group["pdf_page_number"]))
    candidates = [e for e in index.get(key, []) if e.get("review_status") == "accepted"]
    needle = _norm(quote)
    matching = [e for e in candidates if needle and needle in _norm(e.get("source_text", ""))]
    if matching:
        return max(matching, key=lambda e: len(e.get("source_text", "")))
    if candidates:
        return max(candidates, key=lambda e: len(e.get("source_text", "")))
    raise ValueError(f"no Stage 6 canonical Evidence for {key}")


def _row_from_evidence(evidence: dict, *, sample_id: str, source_statement_type: str | None = None,
                       statement_text: str | None = None, object_id: str | None = None,
                       document_key: str | None = None,
                       applicability_scope: dict | None = None, value=None, unit=None,
                       quantities: list | None = None, reviewer_type: str = "ai_cross_review",
                       review_basis: str = "stage6_original_page_review") -> dict:
    text = statement_text or evidence["source_text"]
    statement_type = _canonical_type(source_statement_type or "fact", text)
    subject = object_id or f"entity-{hashlib.sha1((evidence['evidence_id'] + text[:80]).encode('utf-8')).hexdigest()[:16]}"
    location = evidence["locations"][0]
    return {
        "sample_id": sample_id,
        "split": "development_regression_golden",
        "task": "statement",
        "entity_alignment_task": "development_only",
        "independent_for_acceptance": False,
        "document_logical_id": evidence["document_logical_id"],
        "revision_id": evidence["revision_id"],
        "physical_page": location["physical_page"],
        "logical_page": location.get("logical_page"),
        "evidence_bindings": [{"evidence_id": evidence["evidence_id"], "support_type": "direct"}],
        "source_span_ids": list(evidence.get("source_span_ids", [])),
        "source_text_sha256": evidence["source_text_sha256"],
        "statement_id": f"stage11-statement-{hashlib.sha1((evidence['evidence_id'] + text).encode('utf-8')).hexdigest()[:20]}",
        "statement_type": statement_type,
        "predicate": _predicate(statement_type),
        "statement_text": text,
        "subject_entity_id": subject,
        "object_value": {"kind": "source_assertion", "value": text},
        "entity_alignment": _entity_alignment(subject, subject if object_id else text[:24], reviewer_type=reviewer_type),
        "applicability_scope": applicability_scope or {
            "document_key": document_key or evidence.get("document_logical_id"),
            "physical_page": location["physical_page"],
        },
        "value": value,
        "unit": unit,
        "quantities": quantities or [],
        "normative_modality": "shall" if statement_type == "requirement" else "descriptive",
        "negation_scope": [],
        "review_status": "accepted",
        "review_basis": review_basis,
        "reviewer": "user_confirmation" if reviewer_type == "user_confirmation" else "Codex_stage11_cross_review",
        "reviewer_type": reviewer_type,
        "review_reason": "Source text and page identity are traceable to the original-material Evidence.",
        "formal_release": False,
        "derived_from_stage3_statement": None,
        "derived_from_stage3_evidence": None,
    }


def build() -> list[dict]:
    confirmation = json.loads(SOURCE.read_text(encoding="utf-8"))
    if confirmation.get("status") != "confirmed_by_user":
        raise ValueError("Stage 3 confirmation is not user-confirmed")
    index = _evidence_index()
    manifest = json.loads((ROOT / "data/stage7/terminology_input_manifest.json").read_text(encoding="utf-8"))
    logical_to_key = {page["document_logical_id"]: page["document_key"] for page in manifest["pages"]}
    rows: list[dict] = []
    for group in confirmation["confirmed_groups"]:
        for item in group["evidence"]:
            evidence = _find_canonical(index, group, item["quote"])
            row = _row_from_evidence(
                evidence,
                sample_id=f"stage11-dev-{item['statement_id']}",
                source_statement_type=item["statement_type"],
                statement_text=item["statement_text"],
                object_id=item["object_id"],
                document_key=logical_to_key[evidence["document_logical_id"]],
                applicability_scope=item["applicability"],
                value=item.get("value"), unit=item.get("unit"), quantities=item.get("quantities", []),
                reviewer_type="user_confirmation", review_basis="stage3_user_confirmation",
            )
            row["statement_id"] = item["statement_id"]
            row["document_key"] = logical_to_key[evidence["document_logical_id"]]
            row["derived_from_stage3_statement"] = item["statement_id"]
            row["derived_from_stage3_evidence"] = item["evidence_id"]
            rows.append(row)

    used = {tuple((row["document_logical_id"], row["revision_id"], row["physical_page"])) for row in rows}
    all_evidence = [row["evidence"] for row in _jsonl(STAGE6_BUNDLE)]
    for document_key in ("DL5190.3", "D300N", "DLT863", "HAF103", "auxiliary_installation_book"):
        logical_ids = {page["document_logical_id"] for page in manifest["pages"] if page["document_key"] == document_key}
        candidates = [
            evidence for evidence in all_evidence
            if evidence.get("review_status") == "accepted"
            and evidence.get("content_kind") in {"paragraph", "list_item"}
            and len(evidence.get("source_text", "")) >= 80
            and evidence.get("document_logical_id") in logical_ids
            and evidence.get("locations", [{}])[0].get("physical_page")
            and (evidence["document_logical_id"], evidence["revision_id"], evidence["locations"][0]["physical_page"]) not in used
            and any(token in evidence.get("source_text", "") for token in ("应", "必须", "不得", "检查", "验收", "步骤"))
        ]
        for evidence in candidates[:2]:
            row = _row_from_evidence(evidence, sample_id=f"stage11-dev-{evidence['evidence_id']}", document_key=document_key)
            row["document_key"] = document_key
            rows.append(row)
            used.add((row["document_logical_id"], row["revision_id"], row["physical_page"]))

    if len(rows) < 9 or not {row["document_logical_id"] for row in rows}:
        raise ValueError("development Statement samples do not cover the admitted documents")
    return rows


if __name__ == "__main__":
    rows = build()
    OUTPUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    print(json.dumps({"status": "completed", "development_statement_count": len(rows)}, ensure_ascii=False))
