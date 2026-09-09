"""Assemble the current Stage 5 gate without falsely closing it."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from turbine_kg.settings import PROJECT_ROOT


STAGE5_ROOT = PROJECT_ROOT / "data" / "stage5"
TODAY = date.today().isoformat()


def read(name: str) -> dict:
    return json.loads((STAGE5_ROOT / name).read_text(encoding="utf-8"))


def audit() -> dict:
    input_audit = read(f"stage5_input_audit_{TODAY}.json")
    baseline = read(f"stage5_baseline_benchmark_{TODAY}.json")
    rapidocr = read(f"stage5_rapidocr_sample_benchmark_{TODAY}.json")
    tables = read(f"stage5_table_baseline_{TODAY}.json")
    page_identity = read(f"stage5_page_identity_audit_{TODAY}.json")
    sample = read("stage5_sample_manifest.json")
    low_similarity_scanned = [
        {
            "document_key": item["document_key"],
            "pdf_page": item["pdf_page"],
            "fresh_vs_registered_similarity": item["fresh_vs_registered_similarity"],
            "status": "codex_first_pass_complete_user_escalation_only_if_uncertain",
        }
        for item in rapidocr["records"]
        if not item["source_has_native_text"] and item["fresh_vs_registered_similarity"] < 0.97
    ]
    checks = {
        "sample_manifest_five_documents": len(sample["documents"]) == 5,
        "sample_manifest_36_pages": sample["sample_page_count"] == 36,
        "input_audit_pass": input_audit["status"] == "pass",
        "full_baseline_775_pages": baseline["actual"]["page_count"] == 775,
        "full_baseline_zero_failures": baseline["actual"]["failed_page_count"] == 0,
        "rapidocr_sample_36_pages": rapidocr["actual"]["sample_page_count"] == 36,
        "rapidocr_zero_failures": rapidocr["actual"]["failed_page_count"] == 0,
        "table_candidates_six_pages": tables["candidate_count"] == 6,
        "page_identity_reconciled": page_identity["status"] == "page_identity_reconciled" and all(item["source_page_visual_match"] for item in page_identity["records"]),
    }
    return {
        "schema_version": 1,
        "stage": "5",
        "artifact_kind": "stage5_exit_audit",
        "audited_at": TODAY,
        "status": "awaiting_owner_quality_decisions",
        "owner_confirmed_quality_policy": {
            "content_must_match_original_exactly": True,
            "critical_tokens": ["Chinese characters", "digits", "decimal points", "units", "negation terms"],
            "layout_requirements": ["table row/column and continuation relationships", "formula meaning", "figure/caption association"],
            "similarity_is_acceptance_metric": False,
            "unresolved_visual_content_must_not_enter_structured_evidence": True,
        },
        "checks": checks,
        "completed_scope": {
            "document_count": len(sample["documents"]),
            "page_count": baseline["actual"]["page_count"],
            "golden_sample_page_count": sample["sample_page_count"],
            "rapidocr_sample_page_count": rapidocr["actual"]["sample_page_count"],
            "table_candidate_count": tables["candidate_count"],
            "low_text_record_count_all_pages": baseline["actual"]["low_text_record_count_all_pages"],
            "low_text_candidate_count_excluding_expected_exception_modes": baseline["actual"]["low_text_candidate_count_excluding_expected_exception_modes"],
        },
        "low_similarity_scanned_pages": low_similarity_scanned,
        "blocking_items": [
            "36页样本的数字、小数点、单位、否定词和阅读顺序尚未形成正式人工真值准确率。",
            "6页表格/续表尚未完成单元格文字、表头、行列、合并单元格和续表关系真值。",
            "当前只有 RapidOCR 可用，主引擎、备用交叉复核引擎和最终质量阈值尚未确定。",
            "低相似度扫描页的图示/公式内容尚需决定是否进入后续 Evidence 流程。",
        ],
        "owner_review_needed_in_chat": [
            "确认扫描件主引擎与备用交叉复核引擎；当前可用引擎只有 RapidOCR。",
            "确认如何实施“与原始资料一模一样”的质量门禁，以及复杂公式/图示/表格的人工视觉证据记录方式。",
            "确认 6 页候选表格的单元格真值及 DLT863 第 27–28 页续表关系。",
            "确认低相似度扫描页是否允许进入后续 Evidence 流程。",
            "确认低文本页的统计分母规则：全量记录 3 页，排除 review_required/scan_only 后候选 1 页。",
        ],
        "boundaries": [
            "本审计不把 RapidOCR 与既有文本的相似度当作 OCR 准确率。",
            "规则线检测只产生表格候选区域，不代表单元格解析已通过。",
            "阶段 5 未关闭，不能进入依赖阶段而不保留上述门禁。",
        ],
    }


def main() -> int:
    output = STAGE5_ROOT / f"stage5_exit_audit_{TODAY}.json"
    result = audit()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(output)}, ensure_ascii=False))
    return 0 if all(result["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
