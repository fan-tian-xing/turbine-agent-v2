"""Deterministic, candidate-only terminology discovery for Stage 7."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Iterable

from turbine_kg.documents.ids import stable_id

from .models import CANDIDATE_TYPES
from .validation import content_fingerprint


_CJK_TERM = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]{2,16}")
_NUMBER_UNIT = re.compile(
    r"(?<![\w])[-+]?\d+(?:\.\d+)?\s*(?:mm|cm|m|μm|um|MPa|kPa|Pa|℃|°C|%|毫米|厘米|米|微米|兆帕|千帕|帕|度)(?![\w])",
    re.IGNORECASE,
)
_ACTION_SUFFIXES = ("安装", "检查", "调整", "清洗", "测量", "试验", "运行", "启动", "停机", "紧固", "拆除", "更换", "校验", "验收", "监测", "确认", "记录", "调试", "维护", "润滑", "复位", "抽出")
_PROCESS_SUFFIXES = ("安装", "调试", "启动", "运行", "检修", "维护", "试运", "验收", "拆装", "清洗", "润滑")
_EQUIPMENT_SUFFIXES = ("汽轮机", "发电机", "电动机", "泵", "阀", "箱", "缸", "轴", "瓦", "系统", "装置", "设备", "转子", "定子")
_COMPONENT_SUFFIXES = ("座", "盖", "板", "环", "管", "孔", "室", "螺栓", "弹簧", "猫爪", "滑块", "垫片", "轴颈")
_PHENOMENON_SUFFIXES = ("振动", "泄漏", "磨损", "裂纹", "松动", "超温", "高温", "低压", "失效", "噪声", "异常", "卡涩", "错口", "腐蚀", "污染")
_VERIFICATION_SUFFIXES = ("检查", "试验", "检验", "测量", "验收", "校验", "验证", "监测")
_OCR_VARIANT_CHARS = frozenset("圧夲眞測應與為發")

CAPABILITY_QUESTIONS = (
    {"question_id": "cap-01", "question_template": "某设备在某阶段有哪些要求？", "required_slots": ["equipment", "lifecycle_stage"]},
    {"question_id": "cap-02", "question_template": "某工序包含哪些有序步骤？", "required_slots": ["process"]},
    {"question_id": "cap-03", "question_template": "某参数的数值、范围、单位、位置和工况是什么？", "required_slots": ["parameter", "condition"]},
    {"question_id": "cap-04", "question_template": "某异常涉及哪个具体对象？", "required_slots": ["phenomenon", "object"]},
    {"question_id": "cap-05", "question_template": "多份资料分别适用于什么机型和条件？", "required_slots": ["document", "model", "condition"]},
    {"question_id": "cap-06", "question_template": "当前有哪些证据支持的候选方案？", "required_slots": ["evidence", "option"]},
    {"question_id": "cap-07", "question_template": "多个方案的前提和验证要求分别是什么？", "required_slots": ["option", "condition", "verification"]},
    {"question_id": "cap-08", "question_template": "当前还缺少哪些信息？", "required_slots": ["information_gap"]},
    {"question_id": "cap-09", "question_template": "历史案例当时发生了什么、如何处理、结果如何？", "required_slots": ["case", "action", "result"]},
    {"question_id": "cap-10", "question_template": "当前场景与历史案例有哪些相同点和不同点？", "required_slots": ["current_scene", "case", "comparison"]},
)


def load_stage6_evidence_bundle(path: Path) -> list[dict]:
    """Read the Stage 6 canonical bundle as a bounded sample input.

    This is intentionally a real consumer of the Stage 6 artifact.  The
    bundle is used for page-level cross-checking only and never widened to
    represent full-document Evidence.
    """
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in rows:
        evidence = row.get("evidence", {})
        if evidence.get("review_status") != "accepted":
            raise ValueError("Stage 6 canonical bundle contains non-accepted Evidence")
        if row.get("input", {}).get("physical_page", 0) < 1:
            raise ValueError("Stage 6 canonical bundle contains an invalid page")
    return rows


def normalize_term(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", "", value).strip("，。；：、,.!！?？()（）[]【】")


def _types_for_term(term: str) -> set[str]:
    categories: set[str] = set()
    if term.endswith(_EQUIPMENT_SUFFIXES):
        categories.add("equipment")
    if term.endswith(_COMPONENT_SUFFIXES):
        categories.add("component")
    if term.endswith(_ACTION_SUFFIXES):
        categories.add("action")
    if term.endswith(_PROCESS_SUFFIXES):
        categories.add("process")
    if term.endswith(_PHENOMENON_SUFFIXES):
        categories.add("phenomenon")
    if term.endswith(_VERIFICATION_SUFFIXES):
        categories.add("verification")
    # Do not label every term near a modality word as a requirement.  The
    # candidate layer records only lexical discovery signals; statement-level
    # modality belongs to later Evidence/ontology work.
    if term.endswith(("要求", "规定", "标准", "验收")):
        categories.add("requirement")
    if term.endswith(("工况", "状态", "适用范围", "适用条件")):
        categories.add("applicability_condition")
    return categories


def _questions_for_type(candidate_type: str) -> list[str]:
    mapping = {
        "equipment": ["cap-01", "cap-04", "cap-05"],
        "component": ["cap-01", "cap-04"],
        "action": ["cap-02", "cap-07", "cap-09"],
        "parameter": ["cap-03", "cap-07"],
        "phenomenon": ["cap-04", "cap-06"],
        "process": ["cap-02", "cap-07"],
        "verification": ["cap-03", "cap-07"],
        "requirement": ["cap-01", "cap-03", "cap-05"],
        "applicability_condition": ["cap-03", "cap-05", "cap-07"],
        "ocr_variant_candidate": ["cap-08"],
    }
    return mapping.get(candidate_type, ["cap-04"])


def _add_occurrence(store: dict[tuple[str, str], dict], term: str, candidate_type: str, page: dict, *, method: str, text_origin: str, source_kind: str = "page_text") -> None:
    normalized = normalize_term(term)
    if not normalized or candidate_type not in CANDIDATE_TYPES:
        return
    key = (normalized, candidate_type)
    item = store.setdefault(key, {"normalized_form": normalized, "candidate_type": candidate_type, "occurrences": [], "origins": set(), "documents": set()})
    occurrence = {
        "document_key": page["document_key"],
        "document_logical_id": page["document_logical_id"],
        "revision_id": page["revision_id"],
        "physical_page": page["physical_page"],
        "page_id": page["page_id"],
        "text_fingerprint": page["processing_text_sha256"],
        "text_origin": text_origin,
        "source_kind": source_kind,
    }
    if occurrence not in item["occurrences"]:
        item["occurrences"].append(occurrence)
    item["origins"].add(text_origin)
    item["documents"].add(page["document_logical_id"])
    item["surface_forms"] = item.get("surface_forms", set()) | {term}
    item["discovery_methods"] = item.get("discovery_methods", set()) | {method}


def analyze_terminology(manifest: dict, page_texts: dict[str, str], *, stage6_rows: Iterable[dict] = ()) -> list[dict]:
    """Create stable candidates from only text-accepted manifest pages."""
    accepted = [page for page in manifest["pages"] if page["page_status"] == "text_accepted"]
    by_key = {(row["document_key"], int(row["input"]["physical_page"])): row for row in stage6_rows}
    store: dict[tuple[str, str], dict] = {}
    for page in accepted:
        text = page_texts.get(page["page_id"], "")
        if not text.strip():
            raise ValueError(f"text_accepted page has no text: {page['page_id']}")
        text_origin = "native_text" if page["text_source"] == "native_pdf_text" else "ocr_text"
        for match in _NUMBER_UNIT.finditer(text):
            _add_occurrence(store, match.group(0), "parameter", page, method="numeric_unit_pattern", text_origin=text_origin)
        for match in _CJK_TERM.finditer(text):
            term = match.group(0)
            types = _types_for_term(term)
            if not types:
                continue
            for candidate_type in types:
                method = "lexical_pattern"
                _add_occurrence(store, term, candidate_type, page, method=method, text_origin=text_origin)
                if text_origin == "ocr_text" and any(char in _OCR_VARIANT_CHARS for char in term):
                    _add_occurrence(store, term, "ocr_variant_candidate", page, method=method, text_origin=text_origin)
        # Stage 6 is a bounded verification cross-check, not a source of
        # full-document text.  Looking it up proves the consumer path and
        # protects against accidentally treating sample Evidence as corpus.
        _ = by_key.get((page["document_key"], page["physical_page"]))

    records: list[dict] = []
    for item in store.values():
        origins = sorted(item["origins"])
        candidate_type = item["candidate_type"]
        surface_forms = sorted(item.get("surface_forms", {item["normalized_form"]}))
        is_ocr_variant = candidate_type == "ocr_variant_candidate" or (
            "ocr_text" in origins and any(normalize_term(surface) != surface for surface in surface_forms)
        )
        occurrences = sorted(item["occurrences"], key=lambda value: (value["document_key"], value["physical_page"], value["page_id"]))
        candidate_id = stable_id("term", item["normalized_form"], candidate_type)
        record = {
            "candidate_id": candidate_id,
            "surface_form": surface_forms[0],
            "surface_forms": surface_forms,
            "normalized_form": item["normalized_form"],
            "candidate_type": candidate_type,
            "occurrence_count": len(occurrences),
            "document_frequency": len(item["documents"]),
            "document_frequency_policy": "document_family_equal_weight_discovery_only",
            "occurrences": occurrences[:50],
            "text_origins": origins,
            "is_ocr_variant": is_ocr_variant,
            "review_status": "candidate_only",
            "review_reason": "Stage 7 discovery output; no automatic ontology or runtime vocabulary promotion.",
            "capability_question_ids": _questions_for_type(candidate_type),
            "discovery_method": sorted(item["discovery_methods"])[0],
        }
        record["content_fingerprint"] = content_fingerprint(record)
        records.append(record)
    return sorted(records, key=lambda row: (row["candidate_type"], row["normalized_form"], row["candidate_id"]))


def capability_questions_payload() -> dict:
    return {
        "schema_version": 1,
        "stage": "7",
        "artifact_kind": "business_capability_questions",
        "formal_release": False,
        "producer": "turbine_kg.terminology.analyzer",
        "status": "candidate_templates_only",
        "questions": list(CAPABILITY_QUESTIONS),
        "consumer": "Stage 8 ontology capability mapping and later test design",
        "boundary": "Templates do not execute retrieval, answering, case matching, or Claim validation.",
    }
