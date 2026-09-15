"""Freeze source-grounded Stage 11 Statement/entity holdout samples."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

try:
    import pymupdf
except ImportError:  # pragma: no cover
    import fitz as pymupdf

from turbine_kg.registry.source_inputs import resolve_allowlisted_path
from turbine_kg.settings import Settings


ROOT = Path(__file__).resolve().parents[1]
STAGE7 = ROOT / "data" / "stage7" / "terminology_input_manifest.json"
STAGE6 = ROOT / "data" / "stage6" / "stage6_evidence_golden_sample.json"
ASSETS = ROOT / "data" / "registry" / "source_assets.jsonl"
OUT = ROOT / "data" / "stage11"

DOCUMENTS = ("DL5190.3", "D300N", "DLT863", "HAF103", "auxiliary_installation_book")
HOLDOUT_PAGES = {
    "DL5190.3": (24, 50, 92),
    "D300N": (6, 32, 61),
    "DLT863": (7, 13, 19),
    "HAF103": (2, 14, 21),
    "auxiliary_installation_book": (78, 229, 429),
}
RESERVE_PAGES = {
    "DL5190.3": (26,), "D300N": (8,), "DLT863": (8,), "HAF103": (4,),
    "auxiliary_installation_book": (80,),
}


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:20]}"


def _normalise(value: str) -> str:
    return re.sub(r"\s+", "", value or "").strip()


def _statement_type(text: str) -> str:
    if any(token in text for token in ("应", "必须", "不得", "要求")):
        return "requirement"
    if any(token in text for token in ("步骤", "首先", "然后", "再将", "依次")):
        return "procedure"
    if any(token in text for token in ("检查", "验收", "验证")):
        return "verification"
    return "fact"


def _requires_isolation(page_text: str) -> bool:
    """Conservatively isolate pages whose OCR ordering looks like a table/metadata mix."""
    metadata_markers = ("等级：", "工种：", "行业：", "行为领域编号", "题分")
    return sum(marker in page_text for marker in metadata_markers) >= 2


def _select_quote(page_text: str) -> str | None:
    lines = [re.sub(r"\s+", " ", line).strip() for line in page_text.splitlines()]
    joined = re.sub(r"\s+", " ", page_text).strip()
    sentences = [sentence.strip() for sentence in re.split(r"(?<=[。！？])", joined) if sentence.strip()]
    sentence_candidates = [
        sentence for sentence in sentences
        if len(sentence) >= 28
        and any(token in sentence for token in ("应", "必须", "不得", "检查", "验收", "要求"))
        and not any(token in sentence for token in ("说明书", "目 录", "目录", "D300N-000105ASM", "DL / T 5190.3", "DL/T863-2016"))
    ]
    if sentence_candidates:
        return max(sentence_candidates, key=lambda sentence: (sum(sentence.count(token) for token in ("应", "必须", "不得", "检查", "验收", "要求")), len(sentence)))
    candidates = []
    for line in lines:
        if len(line) < 18 or not any(token in line for token in ("应", "必须", "不得", "检查", "验收", "要求")):
            continue
        if any(token in line for token in ("说明书", "目 录", "目录", "D300N-000105ASM", "DL / T 5190.3", "DL/T863-2016")):
            continue
        candidates.append(line)
    if candidates:
        return max(candidates, key=lambda line: (sum(line.count(token) for token in ("应", "必须", "不得", "检查", "验收", "要求")), len(line)))
    long_lines = [line for line in lines if len(line) >= 24 and not line.isdigit()]
    return max(long_lines, key=len) if long_lines else None


def _quote_bbox(page, quote: str) -> tuple[float, float, float, float] | None:
    needle = _normalise(quote)
    for block in page.get_text("blocks"):
        block_text = re.sub(r"\s+", " ", block[4]).strip()
        if needle and needle in _normalise(block_text):
            return tuple(round(float(value), 3) for value in block[:4])
    blocks = [block for block in page.get_text("blocks") if block[4].strip()]
    if not blocks:
        return None
    block = max(blocks, key=lambda value: len(value[4]))
    return tuple(round(float(value), 3) for value in block[:4])


def _asset_index() -> dict[str, dict]:
    return {row["asset_id"]: row for row in _jsonl(ASSETS)}


def _page_index() -> dict[tuple[str, int], dict]:
    return {(row["document_key"], int(row["physical_page"])): row for row in json.loads(STAGE7.read_text(encoding="utf-8"))["pages"]}


def _dev_pages() -> set[tuple[str, int]]:
    return {(row["document_key"], int(row["physical_page"])) for row in json.loads(STAGE6.read_text(encoding="utf-8"))["records"]}


def build() -> tuple[list[dict], list[dict], dict]:
    settings = Settings.from_environment()
    assets = _asset_index()
    pages = _page_index()
    dev_pages = _dev_pages()
    registry_rows: list[dict] = []
    evidence_rows: list[dict] = []
    sample_rows: list[dict] = []

    # Register the frozen Stage 6 pages once, independently of the Statement rows.
    # The 15 Stage 3 trial pages are a subset of this same page set.
    for document_key, physical_page in sorted(dev_pages):
        page_meta = pages[(document_key, physical_page)]
        registry_rows.append({
            "sample_id": _id("stage11-dev-page", document_key, physical_page),
            "split": "development_regression_golden",
            "task": "page_ocr_evidence_statement_entity_alignment",
            "document_key": document_key,
            "document_logical_id": page_meta["document_logical_id"],
            "revision_id": page_meta["revision_id"],
            "physical_page": physical_page,
            "source_text_sha256": page_meta.get("analysis_text_sha256"),
            "prior_exposure": ["stage5_ocr_layout_selection", "stage6_evidence_contract_and_quality"],
            "independence": {"statement": False, "entity_alignment_algorithm": False},
            "label_artifact": "data/stage11/stage11_statement_development_samples.jsonl",
            "evidence_artifact": "data/stage6/stage6_evidence_bundle.jsonl",
            "allowed_consumers": ["stage11_exit_audit", "stage11_semantic_review"],
            "review_status": "frozen",
            "frozen": True,
        })

    for document_key in DOCUMENTS:
        selected = list(HOLDOUT_PAGES[document_key])
        for physical_page in selected:
            page = pages[(document_key, physical_page)]
            if page["page_status"] != "text_accepted" or (document_key, physical_page) in dev_pages:
                raise ValueError(f"invalid holdout page selection: {document_key} p{physical_page}")
        logical_id = pages[(document_key, selected[0])]["document_logical_id"]
        revision_id = pages[(document_key, selected[0])]["revision_id"]
        original_asset = next(asset for asset in assets.values() if asset.get("document_logical_id") == logical_id and asset.get("asset_kind") == "original")
        pdf_path = resolve_allowlisted_path(
            pages[(document_key, selected[0])]["processing_relative_path"],
            settings.source_root,
            settings.ocr_derived_root,
        )
        pdf = pymupdf.open(pdf_path)
        for physical_page in selected:
            page_meta = pages[(document_key, physical_page)]
            pdf_page = pdf[physical_page - 1]
            quote = _select_quote(pdf_page.get_text("text"))
            if not quote:
                raise ValueError(f"holdout page has no reviewable text: {document_key} p{physical_page}")
            bbox = _quote_bbox(pdf_page, quote)
            source_span_id = _id("stage11-span", logical_id, revision_id, physical_page, quote)
            evidence_id = _id("stage11-evidence", revision_id, physical_page, quote, "requirement_source")
            evidence_version_id = _id("stage11-evver", revision_id, source_span_id)
            source_hash = _sha(quote)
            sample_id = _id("stage11-holdout", document_key, physical_page)
            subject_id = _id("stage11-entity", document_key, physical_page, quote[:40])
            statement_type = _statement_type(quote)
            isolated = _requires_isolation(pdf_page.get_text("text"))
            sample_review_status = "isolated" if isolated else "pending_manual_review"
            evidence_review_status = "isolated" if isolated else "pending_manual_review"
            evidence_rows.append({
                "sample_id": sample_id,
                "evidence_id": evidence_id,
                "evidence_version_id": evidence_version_id,
                "document_key": document_key,
                "document_logical_id": logical_id,
                "revision_id": revision_id,
                "authority_asset_id": original_asset["asset_id"],
                "processing_asset_id": page_meta["processing_asset_id"],
                "physical_page": physical_page,
                "logical_page": page_meta.get("logical_page"),
                "source_span_id": source_span_id,
                "bbox": bbox,
                "source_text": quote,
                "evidence_status": "candidate",
                "source_confirmation_status": evidence_review_status,
                "page_analysis_text_sha256": page_meta.get("analysis_text_sha256"),
                "evidence_text_sha256": source_hash,
                "source_text_sha256": source_hash,
                "effective_text": quote,
                "review_status": evidence_review_status,
                "reviewer": "stage11_candidate_builder",
                "reviewer_type": "candidate_generation",
                "review_reason": "Evidence candidate requires independent source and layout review before acceptance." if not isolated else "OCR reading order or table metadata is uncertain; isolated pending page-level review.",
                "review_plan": [
                    {"round": 1, "reviewer_role": "independent_source_reviewer", "scope": "original_page_and_source_span"},
                    {"round": 2, "reviewer_role": "independent_semantic_reviewer", "scope": "numeric_unit_negation_scope"},
                ],
                "support_type": "direct",
                "formal_release": False,
            })
            sample_rows.append({
                "sample_id": sample_id,
                "split": "acceptance_holdout",
                "task": "statement",
                "label_status": "candidate_only",
                "entity_alignment_task": "entity_alignment_algorithm",
                "independent_for_statement": True,
                "independent_for_entity_alignment_algorithm": True,
                "independent_for_terminology": False,
                "independent_for_ontology_vocabulary": False,
                "document_key": document_key,
                "document_logical_id": logical_id,
                "revision_id": revision_id,
                "physical_page": physical_page,
                "logical_page": page_meta.get("logical_page"),
                "page_analysis_text_sha256": page_meta.get("analysis_text_sha256"),
                "evidence_text_sha256": source_hash,
                "source_text_sha256": source_hash,
                "evidence_bindings": [{"evidence_id": evidence_id, "support_type": "direct"}],
                "statement_id": _id("stage11-statement", logical_id, revision_id, physical_page, quote),
                "statement_type": statement_type,
                "predicate": "describes_procedure" if statement_type == "procedure" else "requires" if statement_type == "requirement" else "describes",
                "statement_text": quote,
                "subject_entity_id": subject_id,
                "object_value": {"kind": "source_assertion", "value": quote},
                "entity_alignment": [{
                    "surface_form": quote[:24],
                    "entity_id": subject_id,
                    "entity_class": "UnresolvedEntityCandidate",
                    "match_type": "candidate_source_span",
                    "review_status": sample_review_status,
                }],
                "applicability_scope": {"document_key": document_key},
                "value": None,
                "unit": None,
                "quantities": [],
                "normative_modality": "shall" if statement_type == "requirement" else "descriptive",
                "negation_scope": [],
                "review_status": sample_review_status,
                "review_basis": "stage11_candidate_generation",
                "reviewer": "stage11_candidate_builder",
                "reviewer_type": "candidate_generation",
                "review_reason": "Statement candidate awaits independent semantic annotation; no accepted gold label is generated by the builder.",
                "semantic_review_required": True,
                "review_plan": [
                    {"round": 1, "reviewer_role": "independent_statement_reviewer", "scope": "statement_semantics"},
                    {"round": 2, "reviewer_role": "independent_entity_reviewer", "scope": "entity_alignment_and_applicability"},
                ],
                "formal_release": False,
            })
            registry_rows.append({
                "sample_id": sample_id,
                "split": "acceptance_holdout",
                "task": "statement",
                "document_key": document_key,
                "document_logical_id": logical_id,
                "revision_id": revision_id,
                "physical_page": physical_page,
                "source_text_sha256": source_hash,
                "page_analysis_text_sha256": page_meta.get("analysis_text_sha256"),
                "evidence_text_sha256": source_hash,
                "prior_exposure": ["stage7_terminology_discovery", "stage8_candidate_ontology_review"],
                "independence": {
                    "statement": True,
                    "entity_alignment_algorithm": True,
                    "terminology": False,
                    "ontology_vocabulary": False,
                },
                "label_artifact": "data/stage11/stage11_statement_holdout.jsonl",
                "evidence_artifact": "data/stage11/stage11_holdout_evidence.jsonl",
                "allowed_consumers": ["stage11_exit_audit", "stage11_semantic_review"],
                "review_status": sample_review_status,
                "frozen": True,
            })
        pdf.close()
        for physical_page in RESERVE_PAGES[document_key]:
            page_meta = pages[(document_key, physical_page)]
            if page_meta["page_status"] != "text_accepted" or (document_key, physical_page) in dev_pages:
                raise ValueError(f"invalid reserve page selection: {document_key} p{physical_page}")
            registry_rows.append({
                "sample_id": _id("stage11-reserve", document_key, physical_page),
                "split": "acceptance_holdout_reserve",
                "task": "statement",
                "document_key": document_key,
                "document_logical_id": page_meta["document_logical_id"],
                "revision_id": page_meta["revision_id"],
                "physical_page": physical_page,
                "source_text_sha256": page_meta["analysis_text_sha256"],
                "prior_exposure": ["stage7_terminology_discovery", "stage8_candidate_ontology_review"],
                "independence": {"statement": True, "entity_alignment_algorithm": True},
                "label_artifact": None,
                "evidence_artifact": None,
                "allowed_consumers": ["stage11_holdout_selector"],
                "review_status": "reserved",
                "frozen": True,
            })

    registry = {
        "schema_version": 1,
        "artifact_kind": "evaluation_sample_registry",
        "stage": "11",
        "status": "frozen_for_stage11",
        "formal_release": False,
        "producer": "scripts/build_stage11_holdout.py",
        "consumers": ["scripts/audit_stage11_exit.py", "stage11_semantic_review"],
        "development": {"source": "data/stage6/stage6_evidence_golden_sample.json", "page_count": 36, "trial_page_subset_count": 15},
        "acceptance_holdout": {"page_count": 15, "pages_per_document": 3, "reserve_page_count": 5},
        "blind_test": {"status": "excluded", "read_by_stage11": False, "owner": "user-held evaluation boundary"},
        "records": registry_rows,
    }
    return sample_rows, evidence_rows, registry


if __name__ == "__main__":
    samples, evidence, registry = build()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "stage11_statement_holdout.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in samples), encoding="utf-8")
    (OUT / "stage11_holdout_evidence.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in evidence), encoding="utf-8")
    (OUT / "evaluation_sample_registry.json").write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"holdout_pages": len(samples), "evidence": len(evidence), "registry_records": len(registry["records"])}, ensure_ascii=False))
