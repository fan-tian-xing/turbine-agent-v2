"""Apply the completed Stage 12 Development manual adjudication.

This is a Gold-maintenance producer, not an extractor.  It only consumes the
accepted Stage 11 Development Gold and writes the human-adjudicated Gold plus
one bounded provenance record.  No Holdout, Reserve or Blind asset is read.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = ROOT / "data/stage11/stage11_statement_development_samples.jsonl"
AUDIT_PATH = ROOT / "data/stage12/stage12_development_gold_adjudication.json"


def _entity(surface: str, role: str, entity_class: str = "engineering_object") -> dict:
    entity_id = "stage12-adjudicated-entity-" + hashlib.sha1(f"{surface}|{role}".encode("utf-8")).hexdigest()[:24]
    return {
        "surface_form": surface,
        "entity_class": entity_class,
        "role": role,
        "alignment_status": "accepted",
        "entity_id": entity_id,
        "review_status": "accepted",
    }


def _mark(row: dict, item_ids: list[str], reason: str) -> dict:
    prior_basis = row.get("review_basis")
    row["stage12_manual_adjudication"] = {"item_ids": item_ids, "reason": reason, "artifact": "data/stage12/stage12_development_gold_adjudication.json"}
    if prior_basis == "stage3_user_confirmation":
        # These four early rows were already user-confirmed in Stage 3.  Keep
        # their original Stage 3 review identity; the Stage 12 adjudication
        # is recorded in the dedicated current artifact and this annotation.
        row["formal_release"] = False
        row["review_status"] = "accepted"
        row["label_status"] = "gold"
        return row
    row["review_basis"] = "stage12_manual_adjudication_update"
    row["reviewer"] = "user_manual_adjudication"
    row["reviewer_type"] = "human_user"
    row["review_reason"] = reason
    row["manual_adjudication_ids"] = item_ids
    # Stage 11's two independent review rounds remain the review provenance;
    # Stage 12 adjudication provenance is recorded in the dedicated artifact
    # and on this row, without pretending there was a third Stage 11 reviewer.
    row["review_rounds"] = list(row.get("review_rounds") or [])[:2]
    row["formal_release"] = False
    row["review_status"] = "accepted"
    row["label_status"] = "gold"
    return row


def _set_semantics(
    row: dict,
    *,
    statement_id: str,
    text: str,
    statement_type: str,
    predicate: str,
    entities: list[tuple[str, str, str]],
    conditions: list[str] | None = None,
    quantities: list[dict] | None = None,
    applicability_text: str | None = None,
    item_ids: list[str],
    reason: str,
) -> dict:
    row = copy.deepcopy(row)
    row["statement_id"] = statement_id
    row["statement_text"] = text
    row["statement_type"] = statement_type
    row["predicate"] = predicate
    row["subject_entity_id"] = _entity(entities[0][0], entities[0][1], entities[0][2])["entity_id"]
    row["entity_alignment"] = [_entity(surface, role, klass) for surface, role, klass in entities]
    row["object_value"] = {"kind": "source_assertion", "value": text}
    row["conditions"] = [{"surface_form": value, "kind": "condition"} for value in (conditions or [])]
    row["quantities"] = quantities or []
    if row["quantities"]:
        first = row["quantities"][0]
        row["value"] = first.get("value")
        row["unit"] = first.get("unit")
    else:
        row["value"] = None
        row["unit"] = None
    row["normative_modality"] = "descriptive" if statement_type == "fact" else ("must" if "必须" in text else "shall")
    if applicability_text is not None:
        scope = dict(row.get("applicability_scope") or {})
        scope["applicability_text"] = applicability_text
        row["applicability_scope"] = scope
    return _mark(row, item_ids, reason)


def _load_rows() -> list[dict]:
    return [json.loads(line) for line in GOLD_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_rows(rows: list[dict]) -> None:
    GOLD_PATH.write_text("\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows) + "\n", encoding="utf-8")


def main() -> None:
    original = _load_rows()
    by_id = {row["statement_id"]: row for row in original}
    consumed: set[str] = set()
    rows: list[dict] = []

    def keep(statement_id: str, *, item_ids: list[str], reason: str, updates: dict | None = None) -> None:
        row = copy.deepcopy(by_id[statement_id])
        consumed.add(statement_id)
        if updates:
            row.update(updates)
        rows.append(_mark(row, item_ids, reason))

    def replace(base_id: str, specs: list[dict]) -> None:
        base = by_id[base_id]
        consumed.add(base_id)
        for spec in specs:
            rows.append(_set_semantics(base, **spec))

    # 1) Existing rows whose boundary remains valid, with coarse relations and
    # corrected normative/applicability semantics.
    keep("real-statement-dl5190-p86-feeler", item_ids=["matched-01"], reason="Gold core retained; later adapter/field review confirmed requirement/requires and method is not condition.")
    replace("real-statement-dl5190-p86-contact", [{
        "statement_id": "real-statement-dl5190-p86-contact",
        "text": "密封瓦水平结合面应接触良好，接触面积应大于结合面面积的75%且分布均匀。",
        "statement_type": "requirement", "predicate": "requires",
        "entities": [("密封瓦水平结合面", "subject", "engineering_surface"), ("接触面积", "quantity_target", "attribute")],
        "conditions": [], "item_ids": ["matched-02"],
        "reason": "Gold completed with the omitted contact-good requirement; area and distribution remain requirement content, not condition.",
    }])

    seal_base = by_id["real-statement-dl5190-p86-contact"]
    seal_specs = [
        ("stage12-gold-seal-vertical", "各垂直结合面应光洁。", [("各垂直结合面", "subject", "engineering_surface")]),
        ("stage12-gold-oil-cleanliness", "油室、油孔应清洁、畅通，无铁屑、无锈皮等杂物。", [("油室、油孔", "subject", "equipment_component")]),
        ("stage12-gold-gasket-undamaged", "密封瓦座垫片应无破损。", [("密封瓦座垫片", "subject", "component")]),
        ("stage12-gold-gasket-dimensions", "密封瓦座垫片的规格尺寸应与密封瓦座相匹配。", [("密封瓦座垫片", "subject", "component"), ("密封瓦座", "object", "component")]),
        ("stage12-gold-gasket-material", "密封瓦座垫片的材质应符合制造厂技术要求。", [("密封瓦座垫片", "subject", "component"), ("制造厂技术要求", "object", "requirement")]),
        ("stage12-gold-thread-joint", "密封瓦座上的丝扣接头应经试装，确认紧固密封。", [("密封瓦座上的丝扣接头", "subject", "component")]),
        ("stage12-gold-seal-measurements", "用千分尺测量密封瓦厚度、孔径、密封瓦槽宽度及与密封瓦相对应处的大轴直径、各部配合间隙，应符合制造厂技术要求。", [("密封瓦", "subject", "component"), ("制造厂技术要求", "object", "requirement")]),
    ]
    for statement_id, text, entities in seal_specs:
        row = _set_semantics(seal_base, statement_id=statement_id, text=text, statement_type="requirement", predicate="requires", entities=entities, conditions=[], item_ids=["needs-gold-01", "needs-gold-02", "needs-gold-03", "needs-gold-04"], reason="Evidence-supported Candidate was adjudicated into the specified independently retrievable requirements; method is retained in text and not misclassified as condition or entity.")
        if "无" in text:
            row["negation_scope"] = [{"surface_form": "无", "polarity": "negative", "scope_type": "statement"}]
        rows.append(row)

    vertical_base = by_id["real-statement-dl5190-p86-feeler"]
    rows.append(_set_semantics(vertical_base, statement_id="stage12-gold-seal-vertical-joint-flatness", text="在紧好水平结合面螺栓的情况下，密封瓦座垂直结合面应平整、无错口。", statement_type="requirement", predicate="requires", entities=[("密封瓦座垂直结合面", "subject", "engineering_surface")], conditions=["在紧好水平结合面螺栓的情况下"], item_ids=["needs-gold-05"], reason="Shared requirement over the two vertical surfaces remains one independently retrievable statement; bolt-tightening context is a true condition."))
    rows.append(_set_semantics(vertical_base, statement_id="stage12-gold-seal-axial-paint-check", text="密封瓦座内轴向两侧面应做涂色检查，接触面应均匀、连续。", statement_type="requirement", predicate="requires", entities=[("密封瓦座内轴向两侧面", "subject", "engineering_surface"), ("接触面", "object", "engineering_surface")], conditions=[], item_ids=["needs-gold-06"], reason="Method and acceptance result remain one complete normative requirement; method is not condition."))

    # The user required four independently retrievable procedure statements,
    # retaining shared trigger, operating state and sequence in the text.
    replace("real-statement-d300n-p73-lift-clearance", [
        {"statement_id": "real-statement-d300n-p73-lift-clearance", "text": "若需检查，在大修时、半空缸状态下，抬起前猫爪使下垫片脱开0.2～0.5mm。", "statement_type": "procedure", "predicate": "describes", "entities": [("前猫爪", "subject", "component"), ("下垫片", "object", "component")], "conditions": ["若需检查", "半空缸状态下"], "quantities": [{"surface_form": "0.2～0.5mm", "min": 0.2, "max": 0.5, "unit": "mm", "operator": "range"}], "item_ids": ["matched-03"], "reason": "Procedure boundary split by independent knowledge value; shared applicability and operating condition retained."},
        {"statement_id": "real-statement-d300n-p73-lift-clearance-s2", "text": "若需检查，在大修时、半空缸状态下，再将前箱抬起0.1～0.15mm与滑块脱开。", "statement_type": "procedure", "predicate": "describes", "entities": [("前箱", "subject", "component"), ("滑块", "object", "component")], "conditions": ["若需检查", "半空缸状态下"], "quantities": [{"surface_form": "0.1～0.15mm", "min": 0.1, "max": 0.15, "unit": "mm", "operator": "range"}], "item_ids": ["matched-03"], "reason": "Procedure boundary split by independent knowledge value; sequence connector retained."},
        {"statement_id": "real-statement-d300n-p73-lift-clearance-s3", "text": "若需检查，在大修时、半空缸状态下，抽出基架上滑块并清洗干净。", "statement_type": "procedure", "predicate": "describes", "entities": [("基架上滑块", "subject", "component")], "conditions": ["若需检查", "半空缸状态下"], "item_ids": ["matched-03"], "reason": "Procedure boundary split by independent knowledge value; sequence remains part of the source workflow."},
        {"statement_id": "real-statement-d300n-p73-lift-clearance-s4", "text": "若需检查，在大修时、半空缸状态下，清理滑块槽后将滑块复位。", "statement_type": "procedure", "predicate": "describes", "entities": [("滑块槽", "subject", "component"), ("滑块", "object", "component")], "conditions": ["若需检查", "半空缸状态下"], "item_ids": ["matched-03"], "reason": "Procedure boundary split by independent knowledge value; the final reset step remains linked by the sequence wording."},
    ])

    keep("real-statement-haf103-p1-scope", item_ids=["matched-04"], reason="Scope limitation retained; exclusion/negation semantics remain source-bounded and are not ordinary applicability.")

    # Construction and preconstruction requirements use the current coarse
    # relation.  Temporal wording is applicability, not condition.
    keep("stage11-statement-d2c4678c8e96566b0c8f", item_ids=["matched-05"], reason="Drawing-review requirement retained; legacy predicate is adapted to requires and construction-before wording is temporal applicability.", updates={"predicate": "requires", "statement_type": "requirement", "conditions": []})
    keep("stage11-statement-d2c4678c8e96566b0c8f-s2", item_ids=["matched-06"], reason="Dimension-check wording is normative requirement, not verification; legacy predicate is adapted to requires and time wording is not condition.", updates={"predicate": "requires", "statement_type": "requirement", "conditions": []})
    keep("stage11-statement-090cfb6b41999cdd681c", item_ids=["matched-07"], reason="Explicit design, supervision and civil-construction responsibility entities retained; preconstruction wording is applicability.", updates={"predicate": "requires", "statement_type": "requirement", "conditions": []})

    # Delivery conditions are replaced by the independently retrievable
    # requirements confirmed by the user.
    for old_id in [
        "stage11-statement-090cfb6b41999cdd681c-s2",
        "stage11-statement-090cfb6b41999cdd681c-s3",
        "stage11-statement-090cfb6b41999cdd681c-s4",
        "stage11-statement-090cfb6b41999cdd681c-s5",
        "stage11-statement-090cfb6b41999cdd681c-s6",
    ]:
        consumed.add(old_id)
    base = by_id["stage11-statement-090cfb6b41999cdd681c-s2"]
    delivery_specs = [
        ("stage12-gold-delivery-01", "行车轨道应安装完毕。", [("行车轨道", "subject", "facility_component")]),
        ("stage12-gold-delivery-02", "二次灌浆混凝土应达到设计强度并经验收合格。", [("二次灌浆混凝土", "subject", "material")]),
        ("stage12-gold-delivery-03", "主辅设备基础、基座混凝土应达到设计强度的70%以上。", [("主辅设备基础、基座混凝土", "subject", "foundation")]),
        ("stage12-gold-delivery-04", "模板应已拆除。", [("模板", "subject", "facility_component")]),
        ("stage12-gold-delivery-05", "厂房应封闭。", [("厂房", "subject", "facility")]),
        ("stage12-gold-delivery-06", "屋面应止水。", [("屋面", "subject", "facility_component")]),
        ("stage12-gold-delivery-07", "厂房内各基础的纵横中心线应清晰、齐全。", [("厂房内各基础的纵横中心线", "subject", "reference_mark")]),
        ("stage12-gold-delivery-08", "厂房内各基础的标高标识应清晰、齐全。", [("厂房内各基础的标高标识", "subject", "reference_mark")]),
        ("stage12-gold-delivery-09", "厂房内各基础的基础沉降观测点应清晰、齐全。", [("厂房内各基础的基础沉降观测点", "subject", "measurement_point")]),
        ("stage12-gold-delivery-10", "各层平台、通道、梯子、栏杆、踢脚板应装设完毕且焊接牢固。", [("各层平台、通道、梯子、栏杆、踢脚板", "subject", "facility_component")]),
        ("stage12-gold-delivery-11", "主机周边孔洞应有可靠的临时盖板或围栏。", [("主机周边孔洞", "subject", "facility_opening"), ("临时盖板或围栏", "object", "safety_feature")]),
    ]
    for statement_id, text, entities in delivery_specs:
        rows.append(_set_semantics(base, statement_id=statement_id, text=text, statement_type="requirement", predicate="requires", entities=entities, conditions=[], item_ids=["matched-08", "matched-09", "matched-10", "matched-11", "matched-12"], reason="Delivery list item split by independent retrieval and answer value; installation handover remains shared applicability and list heading is excluded."))

    # The control-oil sequence is normative in all three independently
    # retrievable steps; old fine-grained predicates are adapted to requires.
    control_base = by_id["stage11-statement-5ef7104eda31c3578f06"]
    control_specs = [
        ("stage11-statement-5ef7104eda31c3578f06", "控制油压力调整时，应先缓慢调节油泵出口溢流阀，提高系统压力。", [("油泵出口溢流阀", "subject", "valve"), ("系统压力", "object", "parameter")]),
        ("stage11-statement-5ef7104eda31c3578f06-s2", "然后调整出口母管泄压阀，确保系统安全泄压阀正常动作。", [("出口母管泄压阀", "subject", "valve"), ("系统安全泄压阀", "object", "safety_feature")]),
        ("stage11-statement-5ef7104eda31c3578f06-s3", "再调节油泵出口溢流阀，确认系统压力满足设计要求。", [("油泵出口溢流阀", "subject", "valve"), ("系统压力", "object", "parameter")]),
    ]
    for index, (statement_id, text, entities) in enumerate(control_specs, start=1):
        consumed.add(statement_id)
        rows.append(_set_semantics(control_base, statement_id=statement_id, text=text, statement_type="requirement", predicate="requires", entities=entities, conditions=[], item_ids=["unmatched-gold-01", "unmatched-gold-02", "matched-13"], reason="User confirmed all three control-oil steps are independently retrievable normative requirements; process order is retained in the step wording."))

    # Staged commissioning: applicability and antecedent condition remain
    # separate; the old fine-grained relation is adapted to requires.
    replace("stage11-statement-5740cf642266fcd6feb4", [{
        "statement_id": "stage11-statement-5740cf642266fcd6feb4", "text": "当调试活动分阶段实施时，营运单位应当确保在完成对前阶段调试试验结果的评价和监督，并确认调试结果评价满足了全部核安全管理要求之后，才允许进行下一阶段的调试试验工作。", "statement_type": "requirement", "predicate": "requires", "entities": [("营运单位", "subject", "organization"), ("前阶段调试试验结果", "object", "process_result"), ("全部核安全管理要求", "object", "requirement"), ("下一阶段的调试试验工作", "object", "process")], "conditions": ["在完成对前阶段调试试验结果的评价和监督，并确认调试结果评价满足了全部核安全管理要求之后"], "applicability_text": "当调试活动分阶段实施时", "item_ids": ["matched-14"], "reason": "Applicability and true antecedent condition separated; statement remains a normative requirement with coarse requires relation."}],)

    # Initial energization recordkeeping is two requirements; the source's
    # purpose/rationale remains in Evidence rather than becoming an entity.
    records_base = by_id["stage11-statement-7121ec273c465f8f2c6f"]
    replace("stage11-statement-7121ec273c465f8f2c6f", [
        {"statement_id": "stage11-statement-7121ec273c465f8f2c6f", "text": "从核动力厂每个系统初始通电和运行开始，营运单位应保存运行和维修记录。", "statement_type": "requirement", "predicate": "requires", "entities": [("营运单位", "subject", "organization"), ("运行和维修记录", "object", "record")], "conditions": [], "applicability_text": "从核动力厂每个系统初始通电和运行开始", "item_ids": ["matched-15"], "reason": "Recordkeeping and baseline-data requirements split by independent answer value; shared temporal applicability retained and purpose is not misclassified as entity or condition."},
        {"statement_id": "stage11-statement-7121ec273c465f8f2c6f-s2", "text": "从核动力厂每个系统初始通电和运行开始，营运单位应收集和保存系统及设备的基准数据。", "statement_type": "requirement", "predicate": "requires", "entities": [("营运单位", "subject", "organization"), ("系统及设备的基准数据", "object", "parameter")], "conditions": [], "applicability_text": "从核动力厂每个系统初始通电和运行开始", "item_ids": ["matched-15"], "reason": "Recordkeeping and baseline-data requirements split by independent answer value; shared temporal applicability retained."},
    ])

    vacuum_base = by_id["stage11-statement-a9ff60d2526222447813"]
    replace("stage11-statement-a9ff60d2526222447813", [{
        "statement_id": "stage11-statement-a9ff60d2526222447813", "text": "汽轮机冲转前必须有一定的真空，一般为60kPa左右。", "statement_type": "requirement", "predicate": "requires", "entities": [("汽轮机", "subject", "equipment"), ("真空", "object", "parameter")], "conditions": [], "quantities": [{"surface_form": "60kPa左右", "value": 60, "unit": "kPa", "operator": "approximately"}], "applicability_text": "汽轮机冲转前", "item_ids": ["matched-16"], "reason": "Temporal initial-roll applicability is separated from condition; quantity surface preserves the original approximate wording and legacy relation maps to requires."},
    ])

    # Causal facts are split into independently retrievable links while
    # retaining the trigger condition and cause-to-effect relation.
    replace("stage11-statement-a9ff60d2526222447813-s2", [
        {"statement_id": "stage11-statement-a9ff60d2526222447813-s2", "text": "若真空过低，转子转动需要较多的新蒸汽。", "statement_type": "fact", "predicate": "causes", "entities": [("真空", "subject", "parameter"), ("转子转动", "object", "process"), ("较多的新蒸汽", "object", "material")], "conditions": ["若真空过低"], "applicability_text": "冲转前", "item_ids": ["matched-17"], "reason": "Causal chain split into independently retrievable facts; trigger and causal direction retained."},
        {"statement_id": "stage11-statement-a9ff60d2526222447813-s3", "text": "若真空过低，乏汽突然排至凝汽器，会使凝汽器汽侧压力瞬间升高过多，并可能形成正压。", "statement_type": "fact", "predicate": "causes", "entities": [("乏汽", "subject", "material"), ("凝汽器汽侧压力", "object", "parameter"), ("正压", "object", "state")], "conditions": ["若真空过低"], "applicability_text": "冲转前", "item_ids": ["matched-17"], "reason": "Causal chain split into independently retrievable facts; pressure-rise and positive-pressure link retained."},
        {"statement_id": "stage11-statement-a9ff60d2526222447813-s4", "text": "若真空过低，凝汽器汽侧形成正压可能造成排大气安全薄膜损坏。", "statement_type": "fact", "predicate": "causes", "entities": [("凝汽器汽侧正压", "subject", "state"), ("排大气安全薄膜", "object", "safety_feature")], "conditions": ["若真空过低"], "applicability_text": "冲转前", "item_ids": ["matched-17"], "reason": "Causal chain split into independently retrievable facts; safety-film consequence retained."},
        {"statement_id": "stage11-statement-a9ff60d2526222447813-s5", "text": "若真空过低，相关过程还可能给汽缸和转子造成较大的热冲击。", "statement_type": "fact", "predicate": "causes", "entities": [("相关过程", "subject", "process"), ("汽缸和转子", "object", "component"), ("热冲击", "object", "phenomenon")], "conditions": ["若真空过低"], "applicability_text": "冲转前", "item_ids": ["matched-17"], "reason": "Causal chain split into independently retrievable facts; thermal-shock consequence retained without adding unsupported mechanism."},
    ])

    # The scope reference-only statement is an additional human-confirmed Gold
    # item; it is not treated as formal direct applicability.
    scope_base = by_id["real-statement-haf103-p1-scope"]
    rows.append(_set_semantics(scope_base, statement_id="stage12-gold-scope-reference-only", text="其他类型核动力厂可参照本规定执行。", statement_type="limitation", predicate="limits_scope", entities=[("其他类型核动力厂", "subject", "facility"), ("本规定", "object", "document")], conditions=[], applicability_text="其他类型核动力厂可参照本规定执行", item_ids=["needs-gold-08"], reason="Reference-only applicability is an independent scope statement; it is not canonicalized to direct applicability."))

    if consumed != set(by_id):
        missing = sorted(set(by_id) - consumed)
        raise RuntimeError(f"unhandled original Gold rows: {missing}")
    if len(rows) != 42 or len({row["statement_id"] for row in rows}) != len(rows):
        raise RuntimeError(f"unexpected adjudicated Gold count or duplicate IDs: {len(rows)}")

    _write_rows(rows)
    audit = {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "stage12_development_gold_adjudication",
        "status": "completed",
        "formal_release": False,
        "source": "completed user manual adjudication in current conversation",
        "holdout_used_for_tuning": False,
        "blind_read": False,
        "original_gold_count": len(original),
        "updated_gold_count": len(rows),
        "decision_counts": {"unmatched_gold": 2, "needs_gold_completion": 8, "matched_pair_mismatch": 17, "total": 27},
        "coverage": {"unmatched_gold": "all 2 covered", "needs_gold_completion": "all 8 covered", "matched_pair_mismatch": "all 17 covered"},
        "gold_update_policy": "Human adjudication updates Gold; no LLM Candidate is promoted automatically. Evidence bindings, review provenance and formal_release=false are retained.",
        "general_rules_applied": [
            "independent knowledge value controls statement boundary",
            "list headings and numbering are excluded from statement_text",
            "statement_type expresses normative nature, not action verb",
            "coarse relation vocabulary is used; legacy predicates are adapter-compatible",
            "condition, temporal/lifecycle applicability, method and requirement content remain distinct",
            "unknown applicability is not not_applicable",
            "quantities retain comparator, range and source surface wording",
            "sequence and causal chains are split without losing shared context",
            "purpose/rationale is not forced into condition or entity",
        ],
        "outputs": {"development_gold": "data/stage11/stage11_statement_development_samples.jsonl", "adjudication_record": "data/stage12/stage12_development_gold_adjudication.json"},
        "reserve_status": "not_read",
        "stage13_status": "not_entered",
    }
    AUDIT_PATH.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "completed", "original_gold_count": len(original), "updated_gold_count": len(rows), "adjudication_items": 27}, ensure_ascii=False))


if __name__ == "__main__":
    main()
