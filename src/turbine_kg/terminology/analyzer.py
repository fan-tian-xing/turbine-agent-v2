"""Deterministic, candidate-only terminology discovery for Stage 7."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from turbine_kg.documents.ids import stable_id

from .models import CANDIDATE_TYPES
from .validation import content_fingerprint


_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
DEFAULT_CONTRACT_PATH = Path(__file__).resolve().parents[3] / "config" / "terminology_contract.json"

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


def load_terminology_contract(path: Path = DEFAULT_CONTRACT_PATH) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        payload.get("schema_version") != 1
        or payload.get("stage") != "7"
        or set(payload.get("candidate_types", [])) != set(CANDIDATE_TYPES)
        or payload.get("weighting_policy") != "admitted_document_equal_weight_discovery_only"
    ):
        raise ValueError("invalid terminology contract")
    return payload


def _types_for_term(term: str, rules: dict) -> set[str]:
    categories: set[str] = set()
    if term.endswith(tuple(rules["equipment_suffixes"])):
        categories.add("equipment")
    if term.endswith(tuple(rules["component_suffixes"])):
        categories.add("component")
    if term.endswith(tuple(rules["action_suffixes"])):
        categories.add("action")
    if term.endswith(tuple(rules["process_suffixes"])):
        categories.add("process")
    if term.endswith(tuple(rules["phenomenon_suffixes"])):
        categories.add("phenomenon")
    if term.endswith(tuple(rules["verification_suffixes"])):
        categories.add("verification")
    # Do not label every term near a modality word as a requirement.  The
    # candidate layer records only lexical discovery signals; statement-level
    # modality belongs to later Evidence/ontology work.
    if term.endswith(tuple(rules["requirement_suffixes"])):
        categories.add("requirement")
    if term.endswith(tuple(rules["applicability_condition_suffixes"])):
        categories.add("applicability_condition")
    return categories


def _clean_lexical_term(term: str, rules: dict) -> str:
    normalized = normalize_term(term)
    minimum = int(rules.get("min_term_chars", 2))
    maximum = int(rules.get("max_term_chars", 12))
    ignored = set(rules.get("ignored_fragments", []))
    if not minimum <= len(normalized) <= maximum or normalized in ignored:
        return ""
    if normalized.startswith(("的", "其", "并且", "以及")):
        return ""
    return normalized


def _lexical_terms(text: str, rules: dict) -> list[tuple[str, int, int]]:
    suffixes = sorted(
        set(
            rules.get("equipment_suffixes", [])
            + rules.get("component_suffixes", [])
            + rules.get("action_suffixes", [])
            + rules.get("process_suffixes", [])
            + rules.get("phenomenon_suffixes", [])
            + rules.get("verification_suffixes", [])
            + rules.get("requirement_suffixes", [])
            + rules.get("applicability_condition_suffixes", [])
        ),
        key=len,
        reverse=True,
    )
    maximum = int(rules.get("max_term_chars", 12))
    terms: dict[str, tuple[int, int]] = {}
    for match in _CJK_RUN.finditer(text):
        run = match.group(0)
        for suffix in suffixes:
            cursor = 0
            while True:
                hit = run.find(suffix, cursor)
                if hit < 0:
                    break
                end = hit + len(suffix)
                start = max(0, end - maximum)
                context = run[start:end]
                # Keep modality and negation words in the retained surface
                # context.  They are critical evidence, not lexical noise.
                for marker in ("恢复至", "处于", "符合", "下列", "按照", "其中"):
                    marker_start = context.rfind(marker)
                    if 0 <= marker_start < len(context) - len(marker):
                        context = context[marker_start + len(marker):]
                cleaned = _clean_lexical_term(context, rules)
                if cleaned:
                    terms.setdefault(cleaned, (match.start() + start, match.start() + end))
                cursor = end
    return [(term, start, end) for term, (start, end) in sorted(terms.items())]


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
        "synonym_candidate": ["cap-05", "cap-08"],
        "old_name_candidate": ["cap-05", "cap-08"],
        "abbreviation_candidate": ["cap-05", "cap-08"],
        "ocr_variant_candidate": ["cap-08"],
    }
    return mapping.get(candidate_type, ["cap-04"])


def _critical_signals(text: str, start: int, end: int) -> list[str]:
    """Retain only local safety signals; a page-wide signal is not evidence."""
    window = text[max(0, start - 24): min(len(text), end + 24)]
    signals = []
    if re.search(r"不得|严禁|禁止|不应|不能|不可|无须|无需", window):
        signals.append("negation")
    if re.search(r"不大于|不小于|大于等于|小于等于|≤|≥|<|>", window):
        signals.append("comparator")
    if re.search(r"验收|检验|检查|试验", window):
        signals.append("acceptance_or_verification")
    if re.search(r"联锁|保护", window):
        signals.append("interlock_or_protection")
    if re.search(r"适用|工况|状态|条件", window):
        signals.append("applicability")
    return signals


def _add_occurrence(store: dict[tuple[str, str], dict], term: str, candidate_type: str, page: dict, *, method: str, text_origin: str, source_kind: str = "page_text", ocr_variant_signal: bool = False, critical_signals: list[str] | None = None, text_start: int | None = None, text_end: int | None = None) -> None:
    normalized = normalize_term(term)
    normalized = _clean_lexical_term(normalized, {"min_term_chars": 2, "max_term_chars": 64, "ignored_fragments": []}) if candidate_type not in {"synonym_candidate", "old_name_candidate"} else normalized
    if not normalized or candidate_type not in CANDIDATE_TYPES:
        return
    key = (normalized, candidate_type)
    item = store.setdefault(key, {"normalized_form": normalized, "candidate_type": candidate_type, "occurrences": [], "origins": set(), "documents": set(), "ocr_variant_signals": set()})
    occurrence = {
        "document_key": page["document_key"],
        "document_logical_id": page["document_logical_id"],
        "revision_id": page["revision_id"],
        "physical_page": page["physical_page"],
        "page_id": page["page_id"],
        "text_fingerprint": page["analysis_text_sha256"],
        "text_origin": text_origin,
        "source_kind": source_kind,
    }
    if page.get("stage6_evidence_ids"):
        occurrence["evidence_ids"] = page["stage6_evidence_ids"]
    if critical_signals:
        occurrence["critical_signals"] = sorted(set(critical_signals))
    if text_start is not None and text_end is not None:
        occurrence["text_start"] = text_start
        occurrence["text_end"] = text_end
        occurrence["matched_text"] = term
    if occurrence not in item["occurrences"]:
        item["occurrences"].append(occurrence)
    item["origins"].add(text_origin)
    item["documents"].add(page["document_logical_id"])
    item["surface_forms"] = item.get("surface_forms", set()) | {term}
    item["discovery_methods"] = item.get("discovery_methods", set()) | {method}
    if ocr_variant_signal:
        item["ocr_variant_signals"].add(page["page_id"])


def analyze_terminology(manifest: dict, page_texts: dict[str, str], *, stage6_rows: Iterable[dict] = (), contract: dict | None = None) -> list[dict]:
    """Create stable candidates from only text-accepted manifest pages."""
    contract = contract or load_terminology_contract()
    rules = contract["lexical_rules"]
    number_unit = re.compile(rules["number_unit_pattern"], re.IGNORECASE)
    abbreviation = re.compile(rules["abbreviation_pattern"])
    ocr_variant_chars = frozenset(rules["ocr_variant_characters"])
    accepted = [page for page in manifest["pages"] if page["page_status"] == "text_accepted"]
    by_key: dict[tuple[str, int], list[dict]] = {}
    for row in stage6_rows:
        by_key.setdefault((row["document_key"], int(row["input"]["physical_page"])), []).append(row)
    store: dict[tuple[str, str], dict] = {}
    for page in accepted:
        text = page_texts.get(page["page_id"], "")
        if not text.strip():
            raise ValueError(f"text_accepted page has no text: {page['page_id']}")
        is_stage6_text = page["text_source"] == "stage6_accepted_evidence"
        is_ocr = page["processing_asset_id"] != page["authority_asset_id"]
        text_origin = "ocr_text" if is_ocr else "native_text"
        if is_stage6_text:
            canonical_rows = by_key.get((page["document_key"], page["physical_page"]), [])
            if not canonical_rows:
                raise ValueError(f"OCR text accepted without Stage 6 canonical Evidence: {page['page_id']}")
            canonical_ids = {row["evidence"]["evidence_id"] for row in canonical_rows}
            if not set(page.get("stage6_evidence_ids", [])) <= canonical_ids:
                raise ValueError(f"Stage 6 Evidence IDs do not match canonical bundle: {page['page_id']}")
        source_kind = "accepted_stage6_evidence" if is_stage6_text else "page_text"
        for match in number_unit.finditer(text):
            term_ocr_signal = is_ocr and any(character in match.group(0) for character in ocr_variant_chars)
            _add_occurrence(store, match.group(0), "parameter", page, method="numeric_unit_pattern", text_origin=text_origin, source_kind=source_kind, ocr_variant_signal=term_ocr_signal, critical_signals=_critical_signals(text, match.start(), match.end()))
        for term, term_start, term_end in _lexical_terms(text, rules):
            types = _types_for_term(term, rules)
            term_ocr_signal = is_ocr and any(character in term for character in ocr_variant_chars)
            term_signals = _critical_signals(text, term_start, term_end)
            for candidate_type in types:
                _add_occurrence(store, term, candidate_type, page, method="lexical_pattern", text_origin=text_origin, source_kind=source_kind, ocr_variant_signal=term_ocr_signal, critical_signals=term_signals, text_start=term_start, text_end=term_end)
            if term_ocr_signal:
                _add_occurrence(store, term, "ocr_variant_candidate", page, method="lexical_pattern", text_origin=text_origin, source_kind=source_kind, ocr_variant_signal=True, critical_signals=term_signals, text_start=term_start, text_end=term_end)
        for match in abbreviation.finditer(text):
            term_ocr_signal = is_ocr and any(character in match.group(0) for character in ocr_variant_chars)
            _add_occurrence(store, match.group(0), "abbreviation_candidate", page, method="lexical_pattern", text_origin=text_origin, source_kind=source_kind, ocr_variant_signal=term_ocr_signal, text_start=match.start(), text_end=match.end())
            if term_ocr_signal:
                _add_occurrence(store, match.group(0), "ocr_variant_candidate", page, method="lexical_pattern", text_origin=text_origin, source_kind=source_kind, ocr_variant_signal=True, text_start=match.start(), text_end=match.end())
        for relation_type, pattern_key in (("synonym_candidate", "synonym_pattern"), ("old_name_candidate", "old_name_pattern")):
            pattern = rules.get(pattern_key)
            if not pattern:
                continue
            for match in re.finditer(pattern, text):
                left = _clean_lexical_term(match.group("left"), rules)
                right = _clean_lexical_term(match.group("right"), rules)
                if not left or not right or left == right:
                    continue
                relation_term = f"{left}→{right}"
                _add_occurrence(store, relation_term, relation_type, page, method="explicit_relation_pattern", text_origin=text_origin, source_kind=source_kind, text_start=match.start("left"), text_end=match.end("right"))
        # Stage 6 is a bounded verification cross-check.  For OCR pages the
        # build producer has already used its accepted effective_text; this
        # lookup prevents a page from being treated as accepted without the
        # canonical Evidence row that authorizes it.

    records: list[dict] = []
    accepted_pages_by_document: dict[str, set[int]] = defaultdict(set)
    for page in accepted:
        accepted_pages_by_document[page["document_key"]].add(int(page["physical_page"]))
    valid_documents = {key for key, pages in accepted_pages_by_document.items() if pages}
    for item in store.values():
        origins = sorted(item["origins"])
        candidate_type = item["candidate_type"]
        surface_forms = sorted(item.get("surface_forms", {item["normalized_form"]}))
        is_ocr_variant = candidate_type == "ocr_variant_candidate"
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
            "document_weighting_policy": contract["weighting_policy"],
            "occurrences": occurrences,
            "text_origins": origins,
            "is_ocr_variant": is_ocr_variant,
            "requires_original_confirmation": origins == ["ocr_text"] and not any(
                occurrence["source_kind"] == "accepted_stage6_evidence" for occurrence in occurrences
            ),
            "ocr_variant_signal": bool(item.get("ocr_variant_signals")),
            "review_status": "candidate_only",
            "review_reason": "Stage 7 discovery output; no automatic ontology or runtime vocabulary promotion.",
            "capability_question_ids": _questions_for_type(candidate_type),
            "discovery_method": sorted(item["discovery_methods"])[0],
        }
        document_occurrence_counts = Counter(item["document_key"] for item in occurrences)
        record["document_occurrence_counts"] = dict(sorted(document_occurrence_counts.items()))
        pages_by_document = defaultdict(set)
        for occurrence in occurrences:
            pages_by_document[occurrence["document_key"]].add(int(occurrence["physical_page"]))
        document_scores = {
            document_key: len(pages_by_document.get(document_key, set())) / len(accepted_pages_by_document[document_key])
            for document_key in sorted(valid_documents)
        }
        record["document_equal_weighted_score"] = round(
            sum(document_scores.values()) / len(document_scores), 6
        ) if document_scores else 0.0
        record["document_equal_weighted_score_breakdown"] = {
            key: round(value, 6) for key, value in document_scores.items()
        }
        if candidate_type in {"synonym_candidate", "old_name_candidate"}:
            left, right = item["normalized_form"].split("→", 1)
            record["relation"] = {"relation_type": candidate_type, "left": left, "right": right}
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
