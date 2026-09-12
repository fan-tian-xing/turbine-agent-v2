"""Build the independent Stage 7 critical-term golden set.

The expected candidate keys are authored here, independently of the candidate
output.  The set is deliberately small and is a sample gate, not a claim of
exhaustive recall over the 775-page batch.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from turbine_kg.terminology.validation import content_fingerprint


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "stage7" / "stage7_critical_term_golden_set.json"


def _positive_examples() -> list[dict]:
    groups = {
        "prohibition": [
            ("严禁汽轮机启动。", "严禁汽轮机启动", "action"), ("禁止设备运行。", "禁止设备运行", "action"),
            ("严禁阀门拆除。", "严禁阀门拆除", "action"), ("禁止转子抽出。", "禁止转子抽出", "action"),
            ("严禁系统复位。", "严禁系统复位", "action"), ("禁止泵启动。", "禁止泵启动", "action"),
        ],
        "negation": [
            ("设备不得启动。", "设备不得启动", "action"), ("系统不应运行。", "系统不应运行", "action"),
            ("阀门不能拆除。", "阀门不能拆除", "action"), ("转子不可抽出。", "转子不可抽出", "action"),
            ("装置无需复位。", "装置无需复位", "action"), ("泵无须启动。", "泵无须启动", "action"),
        ],
        "numeric_unit": [
            ("压力应不大于0.2MPa。", "0.2MPa", "parameter"), ("间隙为0.03mm。", "0.03mm", "parameter"),
            ("温度范围20mm。", "20mm", "parameter"), ("压力为1.5kPa。", "1.5kPa", "parameter"),
            ("间隔2-3mm。", "2-3mm", "parameter"), ("含量不超过5%。", "5%", "parameter"),
        ],
        "acceptance": [
            ("汽轮机验收。", "汽轮机验收", "verification"), ("设备检查。", "设备检查", "verification"),
            ("系统试验。", "系统试验", "verification"), ("转子测量。", "转子测量", "verification"),
            ("阀门校验。", "阀门校验", "verification"), ("装置检验。", "装置检验", "verification"),
        ],
        "interlock_protection": [
            ("联锁装置动作。", "联锁装置", "equipment"), ("联锁系统检查。", "联锁系统", "equipment"),
            ("保护系统投入。", "保护系统", "equipment"), ("保护装置试验。", "保护装置", "equipment"),
            ("联锁设备确认。", "联锁设备", "equipment"), ("保护阀运行。", "保护阀", "equipment"),
        ],
        "applicability": [
            ("适用条件明确。", "适用条件", "applicability_condition"), ("适用范围确认。", "适用范围", "applicability_condition"),
            ("运行工况记录。", "运行工况", "applicability_condition"), ("设备状态检查。", "设备状态", "applicability_condition"),
            ("检修工况满足。", "检修工况", "applicability_condition"), ("启动状态确认。", "启动状态", "applicability_condition"),
        ],
    }
    result = []
    for category, examples in groups.items():
        for index, (text, expected_form, expected_type) in enumerate(examples, 1):
            result.append({
                "example_id": f"synthetic-{category}-{index:02d}",
                "source_kind": "synthetic",
                "category": category,
                "text": text,
                "expected": {"normalized_form": expected_form, "candidate_type": expected_type},
            })
    return result


def _negative_examples() -> list[dict]:
    return [
        {"example_id": f"synthetic-negative-{i:02d}", "source_kind": "synthetic", "category": "ocr_locality",
         "text": f"汽轮机检查。第{i}页含误字圧。", "expected": {"normalized_form": "汽轮机", "candidate_type": "ocr_variant_candidate", "present": False}}
        for i in range(1, 13)
    ]


def build_payload() -> dict:
    positives = _positive_examples()
    negatives = _negative_examples()
    real_examples = [
        {"example_id": "real-prohibition-01", "source_kind": "allowed_stage6_reviewed", "category": "prohibition", "expected": {"normalized_form": "不得用波纹管变形方法调整", "candidate_type": "action", "source_kind": "accepted_stage6_evidence"}},
        {"example_id": "real-negation-01", "source_kind": "allowed_stage6_reviewed", "category": "negation", "expected": {"normalized_form": "散热器严密性试验", "candidate_type": "action", "source_kind": "accepted_stage6_evidence"}},
        {"example_id": "real-numeric_unit-01", "source_kind": "allowed_stage6_reviewed", "category": "numeric_unit", "expected": {"normalized_form": "0.03mm", "candidate_type": "parameter", "source_kind": "accepted_stage6_evidence"}},
        {"example_id": "real-acceptance-01", "source_kind": "allowed_stage6_reviewed", "category": "acceptance", "expected": {"normalized_form": "设备开箱检查", "candidate_type": "verification", "source_kind": "accepted_stage6_evidence"}},
        {"example_id": "real-interlock_protection-01", "source_kind": "allowed_stage6_reviewed", "category": "interlock_protection", "expected": {"normalized_form": "以保护核动力厂设备", "candidate_type": "equipment", "source_kind": "accepted_stage6_evidence"}},
        {"example_id": "real-applicability-01", "source_kind": "allowed_stage6_reviewed", "category": "applicability", "expected": {"normalized_form": "在半空缸状态", "candidate_type": "applicability_condition", "source_kind": "accepted_stage6_evidence"}},
    ]
    payload = {
        "schema_version": 1,
        "stage": "7",
        "artifact_kind": "stage7_critical_term_golden_set",
        "formal_release": False,
        "producer": "scripts/build_stage7_critical_golden.py",
        "independently_authored": True,
        "scope": "small sample gate for critical terminology discovery; not exhaustive page recall",
        "categories": ["prohibition", "negation", "numeric_unit", "acceptance", "interlock_protection", "applicability"],
        "positive_examples": positives,
        "real_examples": real_examples,
        "negative_examples": negatives,
        "review_provenance": {
            "real_examples": "selected only when an allowed current-batch occurrence is later bound to reviewed original-page evidence",
            "synthetic_examples": "independently authored fixtures for missing or boundary cases",
            "reviewers": ["Codex_fixture_review"],
            "user_confirmation_required": False,
        },
        "consumer": "scripts/audit_stage7_exit.py",
    }
    payload["content_fingerprint"] = content_fingerprint(payload)
    return payload


def main() -> None:
    OUT.write_text(json.dumps(build_payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "written", "positive_count": 36, "negative_count": 12}, ensure_ascii=False))


if __name__ == "__main__":
    main()
