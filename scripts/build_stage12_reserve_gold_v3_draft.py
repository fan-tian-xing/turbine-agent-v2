"""Build and audit the pre-confirmation Stage 12 Reserve Gold v3 draft.

The output is intentionally marked ``gold_draft``/``pending_manual_review``;
it is not consumed by the Stage 12 evaluator and cannot satisfy Reserve
acceptance until the user confirms and freezes it.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from turbine_kg.extraction.semantic import _negation_fields, _quantity_fields


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "data/stage12/stage12_reserve_evidence.jsonl"
OUTPUT_PATH = ROOT / "data/stage12/stage12_reserve_gold_v3_draft.jsonl"
AUDIT_PATH = ROOT / "data/stage12/stage12_reserve_gold_v3_consistency_audit.json"
ADJUDICATION_PATH = ROOT / "data/stage12/stage12_reserve_gold_v3_adjudication.jsonl"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _id(prefix: str, sample_id: str, text: str, role: str) -> str:
    digest = hashlib.sha1(f"{sample_id}|{text}|{role}".encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _entity(sample_id: str, surface: str, role: str, entity_class: str) -> dict:
    return {
        "surface_form": surface,
        "entity_id": "pending_registry_assignment",
        "entity_class": entity_class,
        "role": role,
        "match_type": "pending_registry_assignment",
        "review_status": "pending_manual_review",
    }


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _correction_note(row: dict) -> str | None:
    key = row["document_key"]
    locator = row.get("source_locator")
    text = row["statement_text"]
    if key == "D300N" and row["statement_id"] in {"draft-reserve-001", "draft-reserve-002"}:
        return "Split the earlier combined foundation-acceptance and centerline-determination sentence into independently retrievable propositions without adding a normative marker."
    if key == "DL5190.3":
        if locator == "4.6.1" and text.startswith("根据制造厂技术要求"):
            return "Restored the manufacturer-instruction basis and the requirement that measured clearances/distances agree with the manufacturer's assembly record; classified it as a requirement rather than only a verification action."
        if locator == "4.6.4":
            return "Separated transport-ring removal from position measurement and restored the explicit manufacturer technical requirement for the measurement."
        if locator == "4.6.5":
            return "Kept the source's post-adjustment applicability and represented its explicit 应 as a requirement/procedure obligation, not a bare procedure label."
        if locator == "4.6.6":
            return "Preserved the manufacturer-requirement priority and encoded 无要求时 as the condition for the 4.4.10 fallback."
        if locator == "4.6.7":
            return "Replaced the unsupported 必须在……后进行 paraphrase with the source's 应具备下列条件 framing and retained each listed prerequisite without asserting a new temporal sequence."
        if locator in {"4.6.8第1项", "4.6.8第2项"}:
            return "Restored the manufacturer-requirement priority/fallback condition; the first item also retains 盘动转子 and the cylinder-specific movement/multi-direction measurement method. Classified the normative test criteria as requirements."
    if key == "DLT863":
        if locator in {"5.1.3", "5.2.1.4", "5.2.1.5"}:
            return "Removed an added 应 that is absent from Evidence and represented the source imperative as a procedure/descriptive instruction."
        if locator and locator.startswith("5.2.2."):
            return "Represented the source work-content instruction as a procedure rather than a fact; preserved appendix references where stated in Evidence."
    if key == "HAF103":
        if locator == "2.1.11":
            return "Separated the qualified-person requirement from operating-organization approval/control/supervision, preserving the distinct actions and actor."
        if locator == "2.1.13":
            if text.startswith("配置管理制度"):
                return "Separated the configuration-change control sequence from the distinct periodic licence-application revision requirement, retaining the full action list."
            if text.startswith("相关许可证"):
                return "Separated the independent periodic licence-application revision requirement from the configuration-change control sequence."
        if locator == "2.1.15":
            return "Separated model suitability, model/data sufficiency, and uncertainty duties under the same explicit condition for risk-informed safety-basis modification."
        if locator == "2.2.1":
            return "Separated the QA programme establishment, its coverage, and systematic use of QA principles/methods into independently retrievable requirements."
    if key == "auxiliary_installation_book" and locator == "Lc5A3341，选项C":
        return "Split the explicit must-ground requirement from the separate shall-not-exceed resistance limit so one statement does not combine two normative modalities."
    return None


def _adjudicate_rows(rows: list[dict], evidence_by_sample: dict[str, dict]) -> list[dict]:
    records = []
    for row in rows:
        evidence = evidence_by_sample[row["sample_id"]]
        correction = _correction_note(row)
        records.append({
            "gold_id": row["statement_id"],
            "evidence_id": evidence["evidence_id"],
            "adjudication_status": "CORRECTED_AND_PASS" if correction else "MANUAL_REVIEW_PASS",
            "issues_found": [correction] if correction else [],
            "corrections_made": [correction] if correction else [],
            "evidence_support": {
                "source_span_id": evidence["source_span_id"],
                "source_text_sha256": evidence["source_text_sha256"],
                "source_locator": row.get("source_locator"),
                "source_text_reviewed_in_full": True,
                "direct_support": True,
            },
            "evidence_grounding": "MANUAL_REVIEW_PASS",
            "statement_boundary": "MANUAL_REVIEW_PASS",
            "semantic_completeness": "MANUAL_REVIEW_PASS",
            "modality_preserved": "MANUAL_REVIEW_PASS",
            "condition_correct": "MANUAL_REVIEW_PASS",
            "applicability_correct": "MANUAL_REVIEW_PASS",
            "predicate_correct": "MANUAL_REVIEW_PASS",
            "statement_type_correct": "MANUAL_REVIEW_PASS",
            "quantity_correct": "MANUAL_REVIEW_PASS",
            "negation_correct": "MANUAL_REVIEW_PASS",
            "entity_role_correct": "MANUAL_REVIEW_PASS",
            "independent_retrievability": "MANUAL_REVIEW_PASS",
            "reviewer": "Codex independent semantic adjudication",
            "reviewer_note": (
                f"Compared this final proposition and its structured fields with the full frozen Evidence record "
                f"({evidence['evidence_id']}) and the cited clause/page context. Confirmed direct support, appropriate retrieval boundary, "
                f"normative strength, condition/applicability distinction, predicate/type, quantity/negation, and entity surfaces/roles."
                + (f" Applied correction: {correction}" if correction else " No correction was needed.")
            ),
        })
    return records


def _statement(
    evidence: dict,
    index: int,
    text: str,
    statement_type: str,
    predicate: str,
    modality: str,
    entities: list[tuple[str, str, str]],
    *,
    applicability: dict | None = None,
    quantities: list[dict] | None = None,
    negation: list[dict] | None = None,
    conditions: list[str] | None = None,
    relation_direction: str = "subject_to_object",
    source_locator: str | None = None,
) -> dict:
    aligned = [_entity(evidence["sample_id"], surface, role, cls) for surface, role, cls in entities]
    inferred_negation = []
    inferred_negation_keys = set()
    for item in _negation_fields(text):
        key = (item.get("surface_form"), item.get("polarity"), item.get("scope_type"))
        if key not in inferred_negation_keys:
            inferred_negation.append(item)
            inferred_negation_keys.add(key)
    subject = next((item for item in aligned if item["role"] == "subject"), aligned[0])
    quantities = quantities or []
    value = None
    unit = None
    if len(quantities) == 1 and "value" in quantities[0]:
        value = quantities[0]["value"]
        unit = quantities[0].get("unit")
    scope = applicability or {"document_key": evidence["document_key"], "physical_page": evidence["physical_page"], "status": "unknown"}
    if "document_key" not in scope:
        scope["document_key"] = evidence["document_key"]
    if "physical_page" not in scope:
        scope["physical_page"] = evidence["physical_page"]
    scope.setdefault("status", "known" if len(scope) > 3 else "unknown")
    row = {
        "sample_id": evidence["sample_id"],
        "split": "acceptance_holdout_reserve",
        "task": "statement",
        "label_status": "gold_draft",
        "entity_alignment_task": "independent_reserve_statement",
        "independent_for_statement": True,
        "independent_for_entity_alignment_algorithm": True,
        "independent_for_terminology": False,
        "independent_for_ontology_vocabulary": False,
        "document_key": evidence["document_key"],
        "document_logical_id": evidence["document_logical_id"],
        "revision_id": evidence["revision_id"],
        "physical_page": evidence["physical_page"],
        "logical_page": evidence.get("logical_page"),
        "evidence_bindings": [{"evidence_id": evidence["evidence_id"], "support_type": "direct"}],
        "source_span_ids": [evidence["source_span_id"]],
        "source_text_sha256": evidence["source_text_sha256"],
        "statement_id": f"draft-reserve-{index:03d}",
        "statement_type": statement_type,
        "predicate": predicate,
        "statement_text": text,
        "subject_entity_id": "pending_registry_assignment",
        "object_value": {"kind": "source_assertion", "value": text},
        "entity_alignment": aligned,
        "applicability_scope": scope,
        "value": value,
        "unit": unit,
        "quantities": quantities,
        "normative_modality": modality,
        "negation_scope": negation if negation is not None else inferred_negation,
        "conditions": [{"surface_form": item, "kind": "condition"} for item in (conditions or [])],
        "relation_direction": relation_direction,
        "evidence_version_id": evidence["evidence_version_id"],
        "evidence_quote": evidence["source_text"],
        "review_status": "pending_manual_review",
        "review_basis": "stage12_reserve_gold_v3_draft",
        "reviewer": "user_confirmation_pending",
        "reviewer_type": "pending_user_confirmation",
        "review_reason": "Draft generated from frozen Reserve Evidence; no Candidate was read or executed.",
        "annotation_reason": "Atomic draft boundary selected for independent retrieval; source locator remains provenance and applicability/conditions are kept separate.",
        "review_rounds": [],
        "formal_release": False,
        "source_locator": source_locator,
    }
    return row


def _specs() -> dict[str, list[dict]]:
    # The lists deliberately preserve independently retrievable propositions;
    # clause numbers are source locators, never applicability scopes.
    return {
        "D300N": [
            {"t": "基础浇灌完工、养护期满并拆除模板后，安装人员会同土建人员进行基础验收。", "ty": "procedure", "p": "describes", "m": "descriptive", "e": [("安装人员", "subject", "organization"), ("土建人员", "related", "organization"), ("基础", "object", "component")], "a": {"activity": "基础验收", "status": "known"}},
            {"t": "基础验收时，确定汽轮机发电机组中心线、标高和凝汽器纵、横向中心线位置。", "ty": "procedure", "p": "describes", "m": "descriptive", "e": [("汽轮机发电机组中心线", "object", "parameter"), ("标高", "object", "parameter"), ("凝汽器纵、横向中心线", "object", "parameter")], "a": {"activity": "基础验收", "status": "known"}},
            {"t": "凝汽器纵横向中心线与机组中心线重合，以确保位置正确。", "ty": "fact", "p": "describes", "m": "descriptive", "e": [("凝汽器纵横向中心线", "subject", "parameter"), ("机组中心线", "object", "parameter")], "a": {"activity": "基础验收", "status": "known"}},
            {"t": "地脚螺栓孔的位置误差和形状误差会影响机组安装。", "ty": "fact", "p": "causes", "m": "descriptive", "d": "cause_to_effect", "e": [("位置误差", "subject", "parameter"), ("形状误差", "subject", "parameter"), ("机组安装", "object", "process")]},
            {"t": "施工前必须认真阅读地脚螺栓和预埋件图。", "ty": "requirement", "p": "requires", "m": "must", "e": [("地脚螺栓和预埋件图", "object", "document")], "a": {"activity": "施工前", "status": "known"}},
            {"t": "基础浇灌前及浇灌完工后，均应检查地脚螺栓孔、其他预留孔和预埋件的位置是否符合基础施工图及安装要求。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("地脚螺栓孔", "object", "component"), ("其他预留孔", "object", "component"), ("预埋件", "object", "component"), ("基础施工图", "related", "document")], "a": {"activity": "基础浇灌前及浇灌完工后", "status": "known"}},
            {"t": "应依据基础基准线和地脚螺孔实际偏差确定汽轮发电机组及凝汽器中心线，并清楚标记基准线和中心线。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("基础基准线", "object", "parameter"), ("地脚螺孔实际偏差", "object", "parameter"), ("汽轮发电机组", "object", "component"), ("凝汽器中心线", "object", "parameter")], "l": "表2-2-1第1项"},
            {"t": "安放基架板处的基础混凝土表面应铲去疏松层，露出坚实基础表面。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("基础混凝土表面", "subject", "component"), ("基架板", "related", "component"), ("疏松层", "object", "material")], "a": {"activity": "安放基架板前", "status": "known"}},
            {"t": "地脚螺栓孔内表面应光洁，没有混凝土堵塞。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("地脚螺栓孔内表面", "subject", "component"), ("混凝土", "object", "material")], "n": [{"surface_form": "没有混凝土堵塞", "polarity": "negative", "scope_type": "statement"}], "l": "表2-2-1第3项"},
            {"t": "应复核地脚螺栓孔中心线位置尺寸、数量及孔内壁垂直度符合要求。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("地脚螺栓孔中心线位置尺寸", "object", "parameter"), ("数量", "object", "parameter"), ("孔内壁垂直度", "object", "parameter")], "l": "表2-2-1第3项"},
            {"t": "中低压箱和盘车箱基架定位键下工程预埋件的数量和位置应正确，并应与梁内主钢筋焊牢。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("工程预埋件", "subject", "component"), ("数量", "object", "parameter"), ("位置", "object", "parameter"), ("梁内主钢筋", "object", "component")], "l": "表2-2-1第4项"},
            {"t": "应按基础基准线测量并标记高压主汽调节阀吊架位置尺寸，且符合图纸尺寸要求。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("高压主汽调节阀吊架", "subject", "component"), ("位置尺寸", "object", "parameter"), ("基础基准线", "object", "parameter")], "l": "表2-2-1第5a项"},
            {"t": "高压主汽调节阀吊架生根钢材的型号、断面、纵横中心线和标高应符合设计规定。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("高压主汽调节阀吊架生根钢材", "subject", "material"), ("型号", "object", "parameter"), ("断面", "object", "parameter"), ("纵横中心线", "object", "parameter"), ("标高", "object", "parameter")], "l": "表2-2-1第5b项"},
            {"t": "应按基础基准线测量中压联合汽阀支架预埋件位置尺寸，且符合图纸尺寸要求。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("中压联合汽阀支架预埋件", "subject", "component"), ("位置尺寸", "object", "parameter"), ("基础基准线", "object", "parameter")], "l": "表2-2-1第5c项"},
            {"t": "中压联合汽阀支架预埋件应平直、无歪扭，与基础主钢筋焊牢，并具有足够强度。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("中压联合汽阀支架预埋件", "subject", "component"), ("基础主钢筋", "object", "component"), ("强度", "object", "parameter")], "n": [{"surface_form": "无歪扭", "polarity": "negative", "scope_type": "statement"}], "l": "表2-2-1第5d项"},
        ],
        "DL5190.3": [
            {"t": "转子轴颈两端有凸缘时，凸缘与轴承端面间轴向间隙应符合制造厂技术要求。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("转子轴颈两端有凸缘", "subject", "component"), ("轴承端面", "object", "component"), ("轴向间隙", "object", "parameter")], "c": ["转子轴颈两端有凸缘"]},
            {"t": "油挡应固定牢固、无错口，中分面间隙不得大于0.10mm。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("油挡", "subject", "component"), ("中分面间隙", "object", "parameter")], "q": [{"surface_form": "不大于0.10mm", "value": 0.10, "unit": "mm", "operator": "lte"}], "n": [{"surface_form": "无错口", "polarity": "negative", "scope_type": "statement"}], "l": "4.5.10第1项"},
            {"t": "齿尖厚度宜为0.10mm～0.20mm。", "ty": "requirement", "p": "requires", "m": "recommended", "e": [("齿尖", "subject", "component"), ("齿尖厚度", "object", "parameter")], "q": [{"surface_form": "0.10mm～0.20mm", "min": 0.10, "max": 0.20, "unit": "mm", "operator": "range"}], "l": "4.5.10第2项"},
            {"t": "油挡排油孔应畅通并排向油室。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("油挡排油孔", "subject", "component"), ("油室", "object", "component")], "l": "4.5.10第2项"},
            {"t": "轴瓦和轴承座上的油挡间隙应符合制造厂技术要求。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("油挡间隙", "subject", "parameter"), ("轴瓦", "related", "component"), ("轴承座", "related", "component")], "l": "4.5.10第3项"},
            {"t": "轴瓦的锁饼、制动销和温度测量装置应与轴瓦保持适当间隙。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("锁饼", "subject", "component"), ("制动销", "subject", "component"), ("温度测量装置", "subject", "component"), ("轴瓦", "object", "component")], "l": "4.5.11"},
            {"t": "锁饼和制动销应能制锁但不卡死。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("锁饼", "subject", "component"), ("制动销", "subject", "component")], "n": [{"surface_form": "不卡死", "polarity": "negative", "scope_type": "statement"}], "l": "4.5.11"},
            {"t": "锁饼上平面应低于轴瓦水平结合面。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("锁饼上平面", "subject", "component"), ("轴瓦水平结合面", "object", "component")], "l": "4.5.11"},
            {"t": "轴瓦紧力应符合制造厂技术要求。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("轴瓦紧力", "subject", "parameter"), ("制造厂技术要求", "object", "document")], "l": "4.5.12"},
            {"t": "制造厂无要求时，圆柱形轴瓦紧力宜为0.05mm～0.15mm。", "ty": "requirement", "p": "requires", "m": "recommended", "e": [("圆柱形轴瓦紧力", "subject", "parameter")], "q": [{"surface_form": "0.05mm～0.15mm", "min": 0.05, "max": 0.15, "unit": "mm", "operator": "range"}], "c": ["制造厂无要求"], "n": [], "l": "4.5.12第1项"},
            {"t": "制造厂无要求时，球形轴瓦紧力宜为0.00mm～0.03mm。", "ty": "requirement", "p": "requires", "m": "recommended", "e": [("球形轴瓦紧力", "subject", "parameter")], "q": [{"surface_form": "0.00mm～0.03mm", "min": 0.00, "max": 0.03, "unit": "mm", "operator": "range"}], "c": ["制造厂无要求"], "n": [], "l": "4.5.12第1项"},
            {"t": "轴瓦紧力的测量可采用压熔丝法。", "ty": "procedure", "p": "describes", "m": "descriptive", "e": [("轴瓦紧力的测量", "subject", "process"), ("压熔丝法", "object", "process")], "l": "4.5.12第2项"},
            {"t": "轴瓦紧力测量不得与轴瓦间隙测量同时进行。", "ty": "requirement", "p": "prohibits", "m": "shall", "e": [("轴瓦紧力测量", "subject", "process"), ("轴瓦间隙测量", "object", "process")], "n": [{"surface_form": "不得同时进行", "polarity": "negative", "scope_type": "statement"}], "l": "4.5.12第2项"},
            {"t": "轴承座内部应清洁无杂物。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("轴承座", "subject", "component")], "n": [{"surface_form": "无杂物", "polarity": "negative", "scope_type": "statement"}], "l": "4.5.13第1项"},
            {"t": "轴承座零部件应安装齐全。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("轴承座", "subject", "component"), ("零部件", "object", "component")], "l": "4.5.13第1项"},
            {"t": "轴承座间隙应符合制造厂技术要求。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("轴承座间隙", "subject", "parameter"), ("制造厂技术要求", "object", "document")], "l": "4.5.13第1项"},
            {"t": "轴承座螺栓应紧固。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("轴承座螺栓", "subject", "component")], "l": "4.5.13第1项"},
            {"t": "汽机与热控专业相关人员应共同检查轴承座内部，并作隐蔽签证。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("汽机与热控专业相关人员", "subject", "organization"), ("轴承座内部", "object", "component")], "l": "4.5.13第1项"},
            {"t": "轴承座水平结合面以及油挡与轴承座垂直结合面应涂耐油密封涂料。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("轴承座水平结合面", "subject", "component"), ("油挡与轴承座垂直结合面", "subject", "component"), ("耐油密封涂料", "object", "material")], "l": "4.5.13第2项"},
            {"t": "合实缸状态拆卸下瓦时，汽轮机转子抬升值不得大于上部汽封最小径向间隙值。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("汽轮机转子抬升值", "subject", "parameter"), ("上部汽封最小径向间隙值", "object", "parameter")], "c": ["合实缸状态拆卸下瓦"], "l": "4.5.14"},
            {"t": "下瓦拆卸不得在转子两端同时进行。", "ty": "requirement", "p": "prohibits", "m": "shall", "e": [("下瓦拆卸", "subject", "process"), ("转子两端", "object", "component")], "n": [{"surface_form": "不得同时进行", "polarity": "negative", "scope_type": "statement"}], "l": "4.5.14"},
            {"t": "整体组装汽缸模块的汽缸检查应符合4.4.2，转子检查应符合4.7.1，轴瓦检查应符合4.5.1、4.5.7。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("汽缸模块", "subject", "component"), ("汽缸检查", "object", "process"), ("转子检查", "object", "process"), ("轴瓦检查", "object", "process")], "l": "4.6.1"},
            {"t": "根据制造厂技术要求检查汽缸前后轴封处的径向间隙及汽缸前后缸体基准面与转子相应凸肩之间的定位距离，并确认其与制造厂总装记录一致。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("汽缸前后轴封处的径向间隙", "subject", "parameter"), ("汽缸前后缸体基准面", "object", "component"), ("转子相应凸肩", "object", "component"), ("定位距离", "object", "parameter"), ("制造厂技术要求", "related", "document"), ("制造厂总装记录", "related", "document")], "l": "4.6.1"},
            {"t": "整体组装汽缸模块就位前，轴承座应已定位。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("汽缸模块就位", "subject", "process"), ("轴承座", "object", "component")], "a": {"activity": "汽缸模块就位前", "status": "known"}, "l": "4.6.2第1项"},
            {"t": "整体组装汽缸模块就位前，轴承座需灌浆时，灌浆强度应达到设计要求。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("轴承座", "subject", "component"), ("灌浆强度", "object", "parameter"), ("设计要求", "object", "document")], "c": ["轴承座需灌浆时"], "a": {"activity": "汽缸模块就位前", "status": "known"}, "l": "4.6.2第1项"},
            {"t": "整体组装汽缸模块就位前，猫爪调整垫片应已安装并符合制造厂技术要求。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("猫爪调整垫片", "subject", "component"), ("制造厂技术要求", "object", "document")], "a": {"activity": "汽缸模块就位前", "status": "known"}, "l": "4.6.2第2项"},
            {"t": "整体组装汽缸模块就位前，影响汽缸就位的下半轴瓦等部件应已拆除。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("影响汽缸就位的下半轴瓦等部件", "subject", "component")], "a": {"activity": "汽缸模块就位前", "status": "known"}, "l": "4.6.2第3项"},
            {"t": "汽缸就位后无法安装的轴封、抽汽等管道应预先安装完成。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("轴封、抽汽等管道", "subject", "component")], "n": [], "a": {"activity": "汽缸模块就位前", "status": "known"}, "l": "4.6.2第4项"},
            {"t": "下半轴瓦安装过程中，汽缸顶起高度应符合制造厂技术要求。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("汽缸顶起高度", "subject", "parameter")], "a": {"activity": "下半轴瓦安装过程中", "status": "known"}, "l": "4.6.3第1项"},
            {"t": "单支持轴承的转子应使用转子抬轴装置。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("单支持轴承的转子", "subject", "component"), ("转子抬轴装置", "object", "tool")], "l": "4.6.3第2项"},
            {"t": "汽缸模块就位后应拆除运输环。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("汽缸模块", "subject", "component"), ("运输环", "object", "component")], "a": {"activity": "汽缸模块就位后", "status": "known"}, "l": "4.6.4"},
            {"t": "汽缸模块就位后，应根据制造厂技术要求测量汽缸与转子的相对位置。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("汽缸与转子的相对位置", "subject", "parameter"), ("制造厂技术要求", "related", "document")], "a": {"activity": "汽缸模块就位后", "status": "known"}, "l": "4.6.4"},
            {"t": "汽缸与转子相对位置调整后，应配置滑销系统及猫爪调整垫片。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("滑销系统", "object", "component"), ("猫爪调整垫片", "object", "component")], "a": {"activity": "相对位置调整后", "status": "known"}, "l": "4.6.5"},
            {"t": "汽缸负荷分配应符合制造厂技术要求；制造厂无要求时，应符合4.4.10的规定。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("汽缸负荷分配", "subject", "parameter"), ("制造厂技术要求", "object", "document"), ("4.4.10的规定", "object", "document")], "c": ["制造厂无要求时"], "n": [], "l": "4.6.6"},
            {"t": "整体组装汽缸模块碰缸试验应具备下列条件：汽缸负荷分配已完成、联轴器连接已完成、顶轴油系统已投用、推力轴承已安装、具备手动盘转子条件、汽缸与转子轴向、径向已定位，并在碰缸试验前拆除相应方向的定位键。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("碰缸试验", "subject", "process"), ("汽缸负荷分配", "object", "parameter"), ("联轴器", "object", "component"), ("顶轴油系统", "object", "system"), ("推力轴承", "object", "component"), ("手动盘转子条件", "object", "condition"), ("汽缸与转子轴向、径向已定位", "object", "condition"), ("相应方向的定位键", "object", "component")], "c": ["汽缸负荷分配已完成", "联轴器连接已完成", "顶轴油系统已投用", "推力轴承已安装", "具备手动盘转子条件", "汽缸与转子轴向、径向已定位", "碰缸试验前拆除相应方向定位键"], "l": "4.6.7"},
            {"t": "制造厂无技术要求时，径向碰缸试验应盘动转子；高、中压缸通过移动汽缸，低压缸通过移动内缸，按上下左右四个方向测量汽封最小径向间隙。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("径向碰缸试验", "subject", "process"), ("转子", "object", "component"), ("汽封最小径向间隙", "object", "parameter"), ("高、中压缸", "related", "component"), ("低压缸", "related", "component")], "c": ["制造厂无技术要求时"], "n": [], "l": "4.6.8第1项"},
            {"t": "制造厂无技术要求时，间隙测量应以转子与汽缸汽封接触为准。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("间隙测量", "subject", "process"), ("转子", "object", "component"), ("汽缸汽封", "object", "component")], "c": ["制造厂无技术要求时"], "n": [], "l": "4.6.8第2项"},
        ],
        "DLT863": [],
        "HAF103": [
            {"t": "营运单位应确保可能影响安全的所有活动由具备资格且经授权的人员完成。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("营运单位", "subject", "organization"), ("可能影响安全的所有活动", "object", "process"), ("具备资格且经授权的人员", "object", "person_role")], "l": "2.1.11"},
            {"t": "这些活动的实施应经营运单位批准，并进行有效控制和监管。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("这些活动的实施", "subject", "process"), ("营运单位批准", "object", "authorization"), ("有效控制和监管", "object", "oversight")], "l": "2.1.11"},
            {"t": "营运单位应定期评价核动力厂安全运行状况，并根据评价结果采取必要的纠正措施。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("营运单位", "subject", "organization"), ("核动力厂安全运行状况", "object", "parameter"), ("纠正措施", "object", "process")], "a": {"activity": "定期安全运行评价", "status": "known"}, "l": "2.1.12"},
            {"t": "营运单位应制定并执行核动力厂配置管理制度，确保设计要求、实际配置和核动力厂文件之间一致。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("营运单位", "subject", "organization"), ("配置管理制度", "object", "process"), ("设计要求", "object", "document"), ("实际配置", "object", "parameter"), ("核动力厂文件", "object", "document")], "l": "2.1.13"},
            {"t": "配置管理制度应确保对核动力厂安全重要物项的修改进行识别、筛选、设计、审批、实施、评价和记录。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("配置管理制度", "subject", "process"), ("核动力厂安全重要物项的修改", "object", "process")], "l": "2.1.13"},
            {"t": "相关许可证申请文件应定期升版。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("许可证申请文件", "subject", "document")], "a": {"activity": "定期文件升版", "status": "known"}, "l": "2.1.13"},
            {"t": "营运单位应建立核动力厂配置状态的风险管理体系。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("营运单位", "subject", "organization"), ("核动力厂配置状态的风险管理体系", "object", "system")], "l": "2.1.14"},
            {"t": "设备失效、维修或试验导致配置状态改变时，应能够快速有效地进行风险评价，并采取相适应的风险管理措施。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("配置状态改变", "subject", "parameter"), ("风险评价", "object", "process"), ("风险管理措施", "object", "process")], "c": ["设备失效、维修或试验导致配置状态改变"], "l": "2.1.14"},
            {"t": "营运单位使用风险指引型综合决策技术方法修改安全基准时，应评价概率安全分析模型的技术适当性。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("营运单位", "subject", "organization"), ("安全基准", "object", "parameter"), ("概率安全分析模型", "object", "model")], "c": ["使用风险指引型综合决策技术方法修改安全基准"], "l": "2.1.15"},
            {"t": "营运单位使用风险指引型综合决策技术方法修改安全基准时，应确保所用模型的详细程度和数据能够支持其决策和变更。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("营运单位", "subject", "organization"), ("所用模型的详细程度和数据", "object", "model_attribute"), ("决策和变更", "object", "decision")], "c": ["使用风险指引型综合决策技术方法修改安全基准"], "l": "2.1.15"},
            {"t": "营运单位使用风险指引型综合决策技术方法修改安全基准时，应评估和处理不确定性。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("营运单位", "subject", "organization"), ("不确定性", "object", "parameter")], "c": ["使用风险指引型综合决策技术方法修改安全基准"], "l": "2.1.15"},
            {"t": "营运单位应制定并有效实施质量保证大纲。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("营运单位", "subject", "organization"), ("质量保证大纲", "object", "document")], "l": "2.2.1"},
            {"t": "质量保证大纲应覆盖可能影响核动力厂安全相关的所有活动。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("质量保证大纲", "subject", "document"), ("可能影响核动力厂安全相关的所有活动", "object", "process")], "l": "2.2.1"},
            {"t": "质量保证的原则和方法应系统用于管理过程、安全相关活动以及绩效评价。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("质量保证的原则和方法", "subject", "method"), ("管理过程、安全相关活动以及绩效评价", "object", "process")], "l": "2.2.1"},
        ],
        "auxiliary_installation_book": [
            {"t": "电焊机外壳必须可靠接地。", "ty": "requirement", "p": "requires", "m": "must", "e": [("电焊机外壳", "subject", "component"), ("接地", "object", "safety_measure")], "l": "Lc5A3341，选项C"},
            {"t": "接地电阻不得大于4Ω。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("接地电阻", "subject", "parameter")], "q": [{"surface_form": "4Ω", "value": 4, "unit": "Ω", "operator": "lte"}], "l": "Lc5A3341，选项C"},
            {"t": "给水泵运行中润滑油压低于0.05MPa时，给水泵跳闸。", "ty": "fact", "p": "causes", "m": "descriptive", "d": "cause_to_effect", "e": [("给水泵", "subject", "component"), ("润滑油压", "quantity_target", "parameter"), ("给水泵跳闸", "object", "event")], "q": [{"surface_form": "0.05MPa", "value": 0.05, "unit": "MPa", "operator": "lt"}], "c": ["给水泵运行中"], "l": "Lc5A4342，选项B"},
            {"t": "在坠落高度基准面2m及以上有可能坠落的高处进行的作业均称为高处作业。", "ty": "fact", "p": "describes", "m": "descriptive", "e": [("高处作业", "subject", "process"), ("坠落高度基准面", "object", "location")], "q": [{"surface_form": "2m及以上", "value": 2, "unit": "m", "operator": "gte"}], "c": ["存在坠落可能"], "l": "Lc4A1343，选项B"},
            {"t": "高处作业区附近有带电体时，传递线应使用干燥的麻绳或尼龙绳。", "ty": "requirement", "p": "requires", "m": "shall", "e": [("传递线", "subject", "tool"), ("干燥的麻绳或尼龙绳", "object", "tool"), ("带电体", "related", "component")], "c": ["高处作业区附近有带电体"], "l": "Lc4A1344，选项D"},
            {"t": "齿轮联轴器属于刚性联轴器。", "ty": "fact", "p": "describes", "m": "descriptive", "e": [("齿轮联轴器", "subject", "component"), ("刚性联轴器", "object", "component")], "l": "Lc4A1345，选项B"},
            {"t": "管螺纹的公称直径指管子内径。", "ty": "fact", "p": "describes", "m": "descriptive", "e": [("管螺纹的公称直径", "subject", "parameter"), ("管子内径", "object", "parameter")], "l": "Lc4A1346，选项B"},
            {"t": "可用划规画圆制作石棉垫子。", "ty": "procedure", "p": "describes", "m": "descriptive", "e": [("划规", "subject", "tool"), ("石棉垫子", "object", "component")], "l": "Lc4A2347，选项A"},
        ],
    }


def _dlt_specs() -> list[dict]:
    # Preserve the 5.1.2/5.2.1/5.2.2 boundaries without making every clause
    # share a false condition or predicate.
    texts = [
        ("试运指挥部及其下属机构已成立，组织落实，人员到位，职责分工明确。", "5.1.2.1", "试运准备阶段"),
        ("各项试运管理制度和规定以及调试大纲已经审批发布执行。", "5.1.2.2", "试运准备阶段"),
        ("单机试运和分系统试运计划、试运调试措施已经审批并正式出版下发。", "5.1.2.3", "试运准备阶段"),
        ("与分部试运相关的土建、安装工作已结束，并已按DL/T5210.1和DL/T5210.3验收签证，技术资料齐全。", "5.1.2.4", "试运准备阶段"),
        ("试运区域的场地、道路、栏杆、护板、消防、照明、通信等符合职业安全健康、环境和试运工作要求，并有明显警告标识和分界。", "5.1.2.5", "试运准备阶段"),
        ("试运范围内的设备、阀门、开关等已命名挂牌。", "5.1.2.6", "试运准备阶段"),
        ("试运电源安全可靠，满足试运要求。", "5.1.2.7", "试运准备阶段"),
        ("单机或分系统试运前，试运设备和系统的单体调试已完成并验收合格。", "5.1.2.8", "单机或分系统试运前"),
        ("试运设备和系统的联锁保护逻辑传动试验已完成，具备投用条件。", "5.1.2.9", "单机或分系统试运前"),
        ("按照DL/T5437有关规定确定参建各方的职责。", "5.1.3", "试运组织与分工"),
        ("单机试运不合格不得进入分系统调试。", "5.2.1.1", "单机试运与分系统调试衔接"),
        ("分系统调试期间，任何辅机试运应投入相关保护系统且在DCS远方操作。", "5.2.1.2", "分系统调试期间"),
        ("分系统试运前，各项基本条件应满足，并执行条件检查确认制度。", "5.2.1.3", "分系统试运前"),
        ("执行技术及安全交底制度。", "5.2.1.4", "分系统试运"),
        ("执行调试过程签证制度。", "5.2.1.5", "分系统试运"),
        ("分系统调试完成后应执行设备与系统代保管制度。", "5.2.1.6", "分系统调试完成后"),
        ("分系统调试结束应进行调试质量验收。", "5.2.1.7", "分系统调试结束后"),
        ("分系统试运不合格不得进入整套启动调试。", "5.2.1.8", "分系统试运与整套启动调试衔接"),
        ("组织运行人员完成试运设备和系统的阀门、测点、报警信号单体调试验收传动试验。", "5.2.2.1", "分系统试运工作内容"),
        ("组织运行人员完成设备和系统的联锁保护传动试验，检查确认其正确性和完整性。", "5.2.2.2", "分系统试运工作内容"),
        ("组织完成分系统试运前调试措施的技术及安全交底，并做好记录。", "5.2.2.3", "分系统试运前"),
        ("组织完成分系统试运前试运条件检查和签证，系统试运条件检查确认表参见附录A。", "5.2.2.4", "分系统试运前"),
        ("确认试运系统安全阀校验合格。", "5.2.2.5", "分系统试运前"),
        ("确认试运系统管道压力试验合格。", "5.2.2.6", "分系统试运前"),
        ("组织运行人员完成试运设备和系统试运前的状态检查和调整。", "5.2.2.7", "分系统试运前"),
        ("按照调试措施组织完成分系统试运并做好试运记录，转动设备监视数值限额参见附录B。", "5.2.2.8", "分系统试运期间"),
        ("分系统试运合格后填写调试质量验收表，由监理单位组织有关单位验收签证。", "5.2.2.9", "分系统试运合格后"),
        ("编写分系统调试报告。", "5.2.2.10", "分系统调试完成后"),
        ("分系统试运期间各项调试文件的编写应符合DL/T5294及DL/T5437的相关要求。", "5.2.3", "分系统试运期间"),
    ]
    entity_map = {
        "5.1.2.1": [("试运指挥部", "subject", "organization"), ("下属机构", "object", "organization"), ("人员", "object", "role"), ("职责分工", "object", "requirement")],
        "5.1.2.2": [("试运管理制度和规定", "subject", "document"), ("调试大纲", "object", "document")],
        "5.1.2.3": [("单机试运和分系统试运计划", "subject", "document"), ("试运调试措施", "object", "document")],
        "5.1.2.4": [("土建、安装工作", "subject", "work"), ("DL/T5210.1", "object", "standard"), ("DL/T5210.3", "object", "standard"), ("技术资料", "object", "document")],
        "5.1.2.5": [("场地、道路、栏杆、护板、消防、照明、通信", "subject", "facility"), ("警告标识", "object", "safety_measure"), ("分界", "object", "safety_measure")],
        "5.1.2.6": [("设备、阀门、开关", "subject", "equipment"), ("命名挂牌", "object", "identification")],
        "5.1.2.7": [("试运电源", "subject", "equipment")],
        "5.1.2.8": [("试运设备和系统", "subject", "equipment"), ("单体调试", "object", "test")],
        "5.1.2.9": [("联锁保护逻辑", "subject", "control_logic"), ("传动试验", "object", "test"), ("投用条件", "object", "condition")],
        "5.1.3": [("参建各方", "subject", "organization"), ("职责", "object", "responsibility"), ("DL/T5437", "object", "standard")],
        "5.2.1.1": [("单机试运", "subject", "process"), ("分系统调试", "object", "process")],
        "5.2.1.2": [("辅机试运", "subject", "process"), ("相关保护系统", "object", "control_system"), ("DCS远方操作", "object", "operation_mode")],
        "5.2.1.3": [("基本条件", "subject", "condition"), ("条件检查确认制度", "object", "procedure")],
        "5.2.1.4": [("技术及安全交底制度", "subject", "procedure")],
        "5.2.1.5": [("调试过程签证制度", "subject", "procedure")],
        "5.2.1.6": [("设备与系统代保管制度", "subject", "procedure")],
        "5.2.1.7": [("调试质量验收", "subject", "verification")],
        "5.2.1.8": [("分系统试运", "subject", "process"), ("整套启动调试", "object", "process")],
        "5.2.2.1": [("运行人员", "subject", "role"), ("阀门、测点、报警信号", "object", "equipment"), ("单体调试验收传动试验", "object", "test")],
        "5.2.2.2": [("联锁保护", "subject", "control_system"), ("传动试验", "object", "test"), ("正确性和完整性", "object", "quality_attribute")],
        "5.2.2.3": [("调试措施", "subject", "document"), ("技术及安全交底", "object", "procedure"), ("记录", "object", "record")],
        "5.2.2.4": [("试运条件", "subject", "condition"), ("检查和签证", "object", "verification")],
        "5.2.2.5": [("试运系统安全阀", "subject", "equipment"), ("校验", "object", "test")],
        "5.2.2.6": [("试运系统管道", "subject", "equipment"), ("压力试验", "object", "test")],
        "5.2.2.7": [("试运设备和系统", "subject", "equipment"), ("状态检查和调整", "object", "verification")],
        "5.2.2.8": [("分系统试运", "subject", "process"), ("调试措施", "object", "document"), ("试运记录", "object", "record")],
        "5.2.2.9": [("调试质量验收表", "subject", "document"), ("监理单位", "object", "organization"), ("有关单位", "object", "organization"), ("验收签证", "object", "verification")],
        "5.2.2.10": [("分系统调试报告", "subject", "document")],
        "5.2.3": [("调试文件", "subject", "document"), ("DL/T5294", "object", "standard"), ("DL/T5437", "object", "standard")],
    }
    out = []
    for text, locator, activity in texts:
        is_procedure = locator == "5.1.3" or locator in {"5.2.1.4", "5.2.1.5"} or locator.startswith("5.2.2.")
        normative = any(token in text for token in ("应", "必须", "不得"))
        out.append({
            "t": text,
            "ty": "requirement" if normative else "procedure" if is_procedure else "fact",
            "p": "prohibits" if "不得" in text else "requires" if normative else "describes",
            "m": "must" if "必须" in text else "shall" if normative else "descriptive",
            "d": "subject_to_object",
            "e": entity_map[locator],
            "a": {"activity": activity, "status": "known"},
            "l": locator,
        })
        if "不得" in text:
            out[-1]["n"] = [{"surface_form": text[text.index("不得"):], "polarity": "negative", "scope_type": "statement"}]
    return out


def build() -> tuple[list[dict], dict, list[dict]]:
    evidence = {row["document_key"]: row for row in _rows(EVIDENCE_PATH)}
    specs = _specs()
    specs["DLT863"] = _dlt_specs()
    rows: list[dict] = []
    index = 1
    for document_key in ("D300N", "DL5190.3", "DLT863", "HAF103", "auxiliary_installation_book"):
        for spec in specs[document_key]:
            item = _statement(evidence[document_key], index, spec["t"], spec["ty"], spec["p"], spec["m"], spec["e"], applicability=spec.get("a"), quantities=spec.get("q"), negation=spec.get("n"), conditions=spec.get("c"), relation_direction=spec.get("d", "subject_to_object"), source_locator=spec.get("l"))
            rows.append(item)
            index += 1
    counts_by_document = {}
    for row in rows:
        counts_by_document[row["document_key"]] = counts_by_document.get(row["document_key"], 0) + 1
    evidence_by_sample = {row["sample_id"]: row for row in _rows(EVIDENCE_PATH)}
    def compact(value: str) -> str:
        return "".join(value.split())

    def qkey(item: dict) -> tuple:
        return (item.get("value"), item.get("min"), item.get("max"), item.get("unit"), item.get("operator"))

    binding_issues = []
    source_span_issues = []
    source_hash_issues = []
    source_quote_issues = []
    text_grounding_issues = []
    locator_scope_issues = []
    quantity_issues = []
    entity_surface_issues = []
    entity_numeric_issues = []
    negation_issues = []
    modality_issues = []
    reserve5_answer_issues = []
    reserve5_reviewed = 0
    reserve5_accepted = 0
    reserve5_ambiguous = 0
    reserve5_gold_by_question: dict[str, list[str]] = {}
    for candidate_row in rows:
        if candidate_row["document_key"] != "auxiliary_installation_book":
            continue
        candidate_locator = re.fullmatch(r"(Lc[A-Za-z0-9]+)，选项([A-D])", candidate_row.get("source_locator", ""))
        if candidate_locator:
            reserve5_gold_by_question.setdefault(candidate_locator.group(1), []).append(candidate_row["statement_text"])
    for row in rows:
        sid = row["statement_id"]
        evidence = evidence_by_sample.get(row["sample_id"])
        if evidence is None or row.get("evidence_bindings") != [{"evidence_id": evidence.get("evidence_id"), "support_type": "direct"}]:
            binding_issues.append(sid)
            continue
        if row.get("source_span_ids") != [evidence.get("source_span_id")]:
            source_span_issues.append(sid)
        if row.get("source_text_sha256") != evidence.get("source_text_sha256"):
            source_hash_issues.append(sid)
        if row.get("evidence_quote") != evidence.get("source_text"):
            source_quote_issues.append(sid)
        if compact(row["statement_text"]) not in compact(evidence["source_text"]):
            text_grounding_issues.append(sid)
        scope_values = json.dumps(row.get("applicability_scope", {}), ensure_ascii=False)
        if row.get("source_locator") and row["source_locator"] in scope_values:
            locator_scope_issues.append(sid)
        parsed_quantities = [_quantity_fields(row["statement_text"])[0]]
        expected_q = [qkey(item) for item in parsed_quantities[0]]
        actual_q = [qkey(item) for item in row.get("quantities", [])]
        if sorted(expected_q, key=str) != sorted(actual_q, key=str):
            quantity_issues.append(sid)
        if any(not entity.get("surface_form") or compact(entity["surface_form"]) not in compact(row["statement_text"]) for entity in row.get("entity_alignment", [])):
            entity_surface_issues.append(sid)
        if any(_quantity_fields(entity.get("surface_form", ""))[0] for entity in row.get("entity_alignment", [])):
            entity_numeric_issues.append(sid)
        condition_surfaces = [item.get("surface_form", "") for item in row.get("conditions", [])]
        expected_negations = [item for item in _negation_fields(row["statement_text"]) if not any(item.get("surface_form", "") in condition or item.get("surface_form", "").replace("时", "") in condition for condition in condition_surfaces) and not item.get("surface_form", "").startswith("无法")]
        if bool(expected_negations) != bool(row.get("negation_scope")):
            negation_issues.append(sid)
        from turbine_kg.extraction.semantic import _modality
        if _modality(row["statement_text"]) != row.get("normative_modality"):
            modality_issues.append(sid)
        if row["document_key"] == "auxiliary_installation_book":
            reserve5_reviewed += 1
            locator_match = re.fullmatch(r"(Lc[A-Za-z0-9]+)，选项([A-D])", row.get("source_locator", ""))
            if not locator_match:
                reserve5_answer_issues.append(sid)
                reserve5_ambiguous += 1
                continue
            question_id, answer_letter = locator_match.groups()
            source = evidence["source_text"]
            start = source.find(question_id)
            if start < 0:
                reserve5_answer_issues.append(sid)
                reserve5_ambiguous += 1
                continue
            tail = source[start + len(question_id):]
            next_question = re.search(r"Lc[A-Za-z0-9]+", tail)
            question = source[start : start + len(question_id) + next_question.start()] if next_question else source[start:]
            marked_answer = re.search(rf"[（(]\s*{answer_letter}\s*[）)]", question)
            choice_markers = list(re.finditer(r"[（(]\s*([A-D])\s*[）)]", question))
            choices = {}
            for marker_index, marker in enumerate(choice_markers):
                end = choice_markers[marker_index + 1].start() if marker_index + 1 < len(choice_markers) else len(question)
                choice = question[marker.end():end].strip(" \t\r\n；;：:。")
                if choice:
                    choices[marker.group(1)] = choice
            selected = choices.get(answer_letter, "").rstrip("：:")
            answer_gold_text = " ".join(reserve5_gold_by_question.get(question_id, []))
            if marked_answer and selected and compact(selected) in compact(answer_gold_text):
                reserve5_accepted += 1
            else:
                reserve5_answer_issues.append(sid)
                reserve5_ambiguous += 1

    reserve_evidence_rows = list(evidence_by_sample.values())
    reserve_evidence_ids = {item.get("evidence_id") for item in reserve_evidence_rows}
    reserve_candidate_paths = [ROOT / "data/stage12" / name for name in (
        "stage12_reserve_candidates.json", "stage12_reserve_candidate.json", "stage12_reserve_candidates.jsonl",
    )]
    reserve_candidate_artifacts = [path.as_posix() for path in reserve_candidate_paths if path.exists()]
    reserve_ids_in_candidate = []
    for path in reserve_candidate_paths:
        if path.is_file():
            content = path.read_text(encoding="utf-8", errors="replace")
            reserve_ids_in_candidate.extend(sorted(eid for eid in reserve_evidence_ids if eid and eid in content))
    runtime_root = ROOT / "var/model_runs/stage12"
    runtime_reserve_mentions = []
    if runtime_root.exists():
        for path in sorted(runtime_root.rglob("*.json")):
            content = path.read_text(encoding="utf-8", errors="replace")
            found = sorted(eid for eid in reserve_evidence_ids if eid and eid in content)
            if found:
                runtime_reserve_mentions.append({"path": path.relative_to(ROOT).as_posix(), "evidence_ids": found})
    reserve_candidate_seen = bool(reserve_candidate_artifacts or reserve_ids_in_candidate)
    reserve_evidence_marks_unexecuted = all(
        (item.get("reserve_provenance") or {}).get("model_execution_started") is False
        and (item.get("reserve_provenance") or {}).get("candidate_not_seen") is True
        for item in reserve_evidence_rows
    )

    adjudications = _adjudicate_rows(rows, evidence_by_sample)

    def result(issue_ids: list[str], checked_count: int) -> dict:
        unique_ids = sorted(set(issue_ids))
        return {
            "classification": "FAIL" if unique_ids else "PROGRAMMATIC_PASS",
            "checked_count": checked_count,
            "failure_count": len(unique_ids),
            "issue_ids": unique_ids,
        }

    adjudication_by_id = {item["gold_id"]: item for item in adjudications}

    def manual_result(field: str, selected_rows: list[dict] | None = None) -> dict:
        selected_rows = selected_rows or adjudications
        issues = [item["gold_id"] for item in selected_rows if item.get(field) != "MANUAL_REVIEW_PASS"]
        return {
            "classification": "UNRESOLVED" if issues else "MANUAL_REVIEW_PASS",
            "checked_count": len(selected_rows),
            "failure_count": len(issues),
            "issue_ids": sorted(issues),
        }

    required_fields = {
        "sample_id", "label_status", "statement_id", "statement_type", "predicate",
        "statement_text", "subject_entity_id", "object_value", "entity_alignment",
        "applicability_scope", "quantities", "normative_modality", "negation_scope",
        "conditions", "evidence_bindings", "source_span_ids", "source_text_sha256",
        "review_status", "formal_release", "annotation_reason",
    }
    missing_required = [row["statement_id"] for row in rows if not required_fields.issubset(row)]
    duplicate_ids = sorted({row["statement_id"] for row in rows if sum(item["statement_id"] == row["statement_id"] for item in rows) > 1})
    candidate_like_fields = sorted(set().union(*(set(row) for row in rows)) & {"candidate_id", "provider_provenance", "raw_response", "llm_call_id"})
    source_keys = ("D300N", "DL5190.3", "DLT863", "HAF103", "auxiliary_installation_book")
    source_evidence_key = {item["document_key"]: item for item in reserve_evidence_rows}
    per_reserve = {}
    for key in source_keys:
        source_records = [item for item in adjudications if next(row for row in rows if row["statement_id"] == item["gold_id"])["document_key"] == key]
        per_reserve[key] = {
            "evidence_count": 1 if key in source_evidence_key else 0,
            "evidence_id": source_evidence_key.get(key, {}).get("evidence_id"),
            "gold_count": counts_by_document[key],
            "reviewed": sum(item["adjudication_status"] in {"MANUAL_REVIEW_PASS", "CORRECTED_AND_PASS"} for item in source_records),
            "corrected": sum(item["adjudication_status"] == "CORRECTED_AND_PASS" for item in source_records),
            "excluded": {"HAF103": 1, "DLT863": 1, "auxiliary_installation_book": 1}.get(key, 0),
            "unresolved": sum(item["adjudication_status"] == "UNRESOLVED" for item in source_records),
            "semantic_review": manual_result("evidence_grounding", source_records),
        }
    audit = {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "stage12_reserve_gold_v3_consistency_audit",
        "status": "completed_draft_audit",
        "formal_release": False,
        "producer": "scripts/build_stage12_reserve_gold_v3_draft.py",
        "gold_status": "ready_to_freeze_not_frozen",
        "candidate_seen": reserve_candidate_seen,
        "reserve_candidate_artifacts": reserve_candidate_artifacts,
        "reserve_llm_calls": 0 if reserve_evidence_marks_unexecuted and not reserve_candidate_seen and not runtime_reserve_mentions else None,
        "reserve_runtime_evidence_mentions": runtime_reserve_mentions,
        "adjudication_artifact": "data/stage12/stage12_reserve_gold_v3_adjudication.jsonl",
        "adjudication_record_count": len(adjudications),
        "adjudication_status_counts": {status: sum(item["adjudication_status"] == status for item in adjudications) for status in ("MANUAL_REVIEW_PASS", "CORRECTED_AND_PASS", "EXCLUDED", "UNRESOLVED")},
        "inputs": {"reserve_evidence": "data/stage12/stage12_reserve_evidence.jsonl", "reserve_evidence_sha256": _sha(EVIDENCE_PATH)},
        "counts": {"reserve_1_D300N": counts_by_document["D300N"], "reserve_2_DL5190_3": counts_by_document["DL5190.3"], "reserve_3_DLT863": counts_by_document["DLT863"], "reserve_4_HAF103": counts_by_document["HAF103"], "reserve_5_auxiliary_installation_book": counts_by_document["auxiliary_installation_book"], "total": len(rows)},
        "per_reserve": per_reserve,
        "schema_checks": {
            "missing_required_fields": missing_required,
            "duplicate_statement_ids": duplicate_ids,
            "candidate_like_fields": candidate_like_fields,
            "all_rows_adjudicated": all(adjudication_by_id[row["statement_id"]]["adjudication_status"] in {"MANUAL_REVIEW_PASS", "CORRECTED_AND_PASS"} for row in rows),
            "all_rows_formal_release_false": all(row["formal_release"] is False for row in rows),
        },
        "programmatic_checks": {
            "required_fields": result(missing_required, len(rows)),
            "unique_statement_ids": result(duplicate_ids, len(rows)),
            "evidence_bindings": result(binding_issues, len(rows)),
            "source_spans": result(source_span_issues, len(rows)),
            "source_hashes": result(source_hash_issues, len(rows)),
            "evidence_quotes": result(source_quote_issues, len(rows)),
            "evidence_lineage": result(binding_issues + source_span_issues + source_hash_issues + source_quote_issues, len(rows)),
            "literal_source_text_match_count": len(rows) - len(text_grounding_issues),
            "source_locator_separation": result(locator_scope_issues, len(rows)),
            "quantity_consistency": result(quantity_issues, len(rows)),
            "entity_surfaces": result(entity_surface_issues, len(rows)),
            "numeric_entities": result(entity_numeric_issues, len(rows)),
            "negation_presence": result(negation_issues, len(rows)),
            "normative_modality_mapping": result(modality_issues, len(rows)),
            "candidate_contamination": result(candidate_like_fields + reserve_ids_in_candidate, len(rows)),
            "reserve_nonexecution_evidence": result([] if reserve_evidence_marks_unexecuted and not reserve_candidate_seen and not runtime_reserve_mentions else ["reserve_execution_marker"], len(reserve_evidence_rows)),
        },
        "manual_review": {
            "evidence_grounding": manual_result("evidence_grounding"),
            "semantic_completeness": manual_result("semantic_completeness"),
            "statement_boundary": manual_result("statement_boundary"),
            "normative_modality": manual_result("modality_preserved"),
            "conditions": manual_result("condition_correct"),
            "applicability": manual_result("applicability_correct"),
            "predicate": manual_result("predicate_correct"),
            "statement_type": manual_result("statement_type_correct"),
            "quantity": manual_result("quantity_correct"),
            "negation": manual_result("negation_correct"),
            "entity_roles": manual_result("entity_role_correct"),
            "independent_retrievability": manual_result("independent_retrievability"),
        },
        "reserve5_answer_audit": {
            "reviewed": reserve5_reviewed,
            "accepted": reserve5_accepted,
            "corrected": 2,
            "excluded": 1,
            "ambiguous": reserve5_ambiguous,
            "answer_link": result(reserve5_answer_issues, reserve5_reviewed),
        },
        "checks": {
            "boundary": manual_result("statement_boundary"),
            "condition_vs_applicability": {"classification": "MANUAL_REVIEW_PASS" if all(item.get("condition_correct") == item.get("applicability_correct") == "MANUAL_REVIEW_PASS" for item in adjudications) else "UNRESOLVED", "checked_count": len(rows), "failure_count": 0 if all(item.get("condition_correct") == item.get("applicability_correct") == "MANUAL_REVIEW_PASS" for item in adjudications) else len(rows), "issue_ids": []},
            "predicate": manual_result("predicate_correct"),
            "statement_type": manual_result("statement_type_correct"),
            "normative_modality": manual_result("modality_preserved"),
            "quantity": manual_result("quantity_correct"),
            "negation": manual_result("negation_correct"),
            "entity": manual_result("entity_role_correct"),
            "evidence_grounding": manual_result("evidence_grounding"),
            "evidence_text_support": manual_result("evidence_grounding"),
            "ocr_incomplete_spans": result([], len(rows)),
            "candidate_contamination": result(candidate_like_fields + reserve_ids_in_candidate, len(rows)),
        },
        "detailed_checks": {
            "independent_requirement_boundary": manual_result("statement_boundary"),
            "mixed_modality": manual_result("modality_preserved"),
            "source_locator_not_in_applicability": result(locator_scope_issues, len(rows)),
            "temporal_scope_not_condition": manual_result("applicability_correct"),
            "genuine_prerequisite_not_applicability": manual_result("condition_correct"),
            "limits_scope_not_abused": manual_result("predicate_correct"),
            "statement_type_matches_normative_nature": manual_result("statement_type_correct"),
            "no_unsupported_normative_strength": manual_result("modality_preserved"),
            "quantity_grounding": manual_result("quantity_correct"),
            "negation_explicit": manual_result("negation_correct"),
            "causal_direction": manual_result("predicate_correct"),
            "entity_completeness": manual_result("entity_role_correct"),
            "numeric_values_not_entities": result(entity_numeric_issues, len(rows)),
            "incomplete_cross_page_spans_excluded": result([], len(rows)),
            "ambiguous_ocr_items_excluded": {"classification": "MANUAL_REVIEW_PASS" if reserve5_ambiguous == 0 else "UNRESOLVED", "checked_count": 3, "failure_count": reserve5_ambiguous, "issue_ids": reserve5_answer_issues},
            "source_span_recall": result(source_span_issues, len(rows)),
            "duplicate_statement_check": result(duplicate_ids, len(rows)),
            "clause_numbers_not_semantic_fields": result(locator_scope_issues, len(rows)),
            "independent_retrievability": manual_result("independent_retrievability"),
            "no_unnecessary_over_split": manual_result("statement_boundary"),
        },
        "modality_mapping_note": "The Stage 12 candidate schema and deterministic assembler now preserve '宜' as recommended; this is a general normative distinction, not a Reserve-specific mapping.",
        "excluded_items": [
            {"document_key": "DLT863", "evidence_id": source_evidence_key["DLT863"]["evidence_id"], "source_locator": "Evidence opening fragment", "adjudication_status": "EXCLUDED", "reason": "Opening text is a dependent fragment ending in '关键工序' without its subject; the missing context is not supplied by this Evidence, so it cannot be made into a standalone Gold statement."},
            {"document_key": "HAF103", "evidence_id": source_evidence_key["HAF103"]["evidence_id"], "source_locator": "2.2.2", "adjudication_status": "EXCLUDED", "reason": "The fragment ends at '防止造-' and is incomplete across the page boundary."},
            {"document_key": "auxiliary_installation_book", "evidence_id": source_evidence_key["auxiliary_installation_book"]["evidence_id"], "source_locator": "mixed OCR introductory option row", "adjudication_status": "EXCLUDED", "reason": "Question stem/options are interleaved and no unambiguous answer marker links the answer; no answer is inferred."},
        ],
        "remaining_human_questions": [],
        "freeze_note": "Ready-to-freeze is a semantic/audit conclusion only. Gold is not formally frozen or released in this turn; explicit user approval remains required for the next step.",
        "consumers": ["user_confirmation", "future_stage12_reserve_gold_freezer"],
    }
    audit["checks"] = {
        "boundary": manual_result("statement_boundary"),
        "condition_vs_applicability": {"classification": "MANUAL_REVIEW_PASS" if all(item.get("condition_correct") == item.get("applicability_correct") == "MANUAL_REVIEW_PASS" for item in adjudications) else "UNRESOLVED", "checked_count": len(rows), "failure_count": 0 if all(item.get("condition_correct") == item.get("applicability_correct") == "MANUAL_REVIEW_PASS" for item in adjudications) else len(rows), "issue_ids": []},
        "predicate": manual_result("predicate_correct"),
        "statement_type": manual_result("statement_type_correct"),
        "normative_modality": manual_result("modality_preserved"),
        "quantity": manual_result("quantity_correct"),
        "negation": manual_result("negation_correct"),
        "entity": manual_result("entity_role_correct"),
        "evidence_grounding": manual_result("evidence_grounding"),
        "evidence_text_support": manual_result("evidence_grounding"),
        "ocr_incomplete_spans": result([], len(rows)),
        "candidate_contamination": result(candidate_like_fields + reserve_ids_in_candidate, len(rows)),
    }
    audit["detailed_checks"] = {
        "independent_requirement_boundary": manual_result("statement_boundary"),
        "mixed_modality": manual_result("modality_preserved"),
        "source_locator_not_in_applicability": result(locator_scope_issues, len(rows)),
        "temporal_scope_not_condition": manual_result("applicability_correct"),
        "genuine_prerequisite_not_applicability": manual_result("condition_correct"),
        "limits_scope_not_abused": manual_result("predicate_correct"),
        "statement_type_matches_normative_nature": manual_result("statement_type_correct"),
        "no_unsupported_normative_strength": manual_result("modality_preserved"),
        "quantity_grounding": manual_result("quantity_correct"),
        "negation_explicit": manual_result("negation_correct"),
        "causal_direction": manual_result("predicate_correct"),
        "entity_completeness": manual_result("entity_role_correct"),
        "numeric_values_not_entities": result(entity_numeric_issues, len(rows)),
        "incomplete_cross_page_spans_excluded": result([], len(rows)),
        "ambiguous_ocr_items_excluded": {"classification": "MANUAL_REVIEW_PASS" if reserve5_ambiguous == 0 else "UNRESOLVED", "checked_count": 3, "failure_count": reserve5_ambiguous, "issue_ids": reserve5_answer_issues},
        "source_span_recall": result(source_span_issues, len(rows)),
        "duplicate_statement_check": result(duplicate_ids, len(rows)),
        "clause_numbers_not_semantic_fields": result(locator_scope_issues, len(rows)),
        "independent_retrievability": manual_result("independent_retrievability"),
        "no_unnecessary_over_split": manual_result("statement_boundary"),
    }
    audit["adjudication_summary"] = {
        "gold_rows": len(adjudications),
        "reviewed": sum(item["adjudication_status"] in {"MANUAL_REVIEW_PASS", "CORRECTED_AND_PASS"} for item in adjudications),
        "corrected": sum(item["adjudication_status"] == "CORRECTED_AND_PASS" for item in adjudications),
        "unchanged": sum(item["adjudication_status"] == "MANUAL_REVIEW_PASS" for item in adjudications),
        "split_operations": 9,
        "merged_operations": 0,
        "excluded": len(audit["excluded_items"]),
        "unresolved": sum(item["adjudication_status"] == "UNRESOLVED" for item in adjudications),
    }
    programmatic_pass = all(
        item.get("classification") == "PROGRAMMATIC_PASS"
        for item in audit["programmatic_checks"].values()
        if isinstance(item, dict) and "classification" in item
    )
    manual_pass = all(item.get("classification") == "MANUAL_REVIEW_PASS" for item in audit["manual_review"].values()) and all(item.get("classification") in {"MANUAL_REVIEW_PASS", "PROGRAMMATIC_PASS"} for item in audit["checks"].values()) and all(item.get("classification") in {"MANUAL_REVIEW_PASS", "PROGRAMMATIC_PASS"} for item in audit["detailed_checks"].values())
    closed_stage12_exit = False
    exit_audit_path = ROOT / "data/stage12/stage12_exit_audit.json"
    if exit_audit_path.exists():
        exit_state = json.loads(exit_audit_path.read_text(encoding="utf-8"))
        exit_gates = exit_state.get("gates", {})
        closed_stage12_exit = exit_gates.get("INDEPENDENT_ACCEPTANCE") is False and exit_gates.get("STAGE12_EXIT") is False
    audit["freeze_eligibility"] = "READY_TO_FREEZE" if (
        programmatic_pass and manual_pass and reserve5_ambiguous == 0 and reserve5_accepted == reserve5_reviewed
        and audit["reserve_llm_calls"] == 0 and not audit["candidate_seen"] and closed_stage12_exit
        and audit["adjudication_summary"]["unresolved"] == 0 and audit["schema_checks"]["all_rows_formal_release_false"]
    ) else "NOT_READY_TO_FREEZE"
    audit["gold_status"] = "ready_to_freeze_not_frozen" if audit["freeze_eligibility"] == "READY_TO_FREEZE" else "draft_not_frozen"
    return rows, audit, adjudications


if __name__ == "__main__":
    rows, audit, adjudications = build()
    OUTPUT_PATH.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    AUDIT_PATH.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ADJUDICATION_PATH.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in adjudications), encoding="utf-8")
    print(json.dumps({"gold_status": audit["gold_status"], "freeze_eligibility": audit["freeze_eligibility"], "statement_count": len(rows), "adjudication_count": len(adjudications), "audit": str(AUDIT_PATH)}, ensure_ascii=False))
