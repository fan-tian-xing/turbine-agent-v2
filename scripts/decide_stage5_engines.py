"""Record the reproducible Stage 5 OCR engine choice."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from turbine_kg.settings import PROJECT_ROOT
from stage5_fingerprint import find_matching_artifact, ocr_fingerprint


STAGE5_ROOT = PROJECT_ROOT / "data" / "stage5"
TODAY = date.today().isoformat()


def _summary(report: dict, similarity_key: str) -> dict:
    values = [item[similarity_key] for item in report["records"]]
    return {
        "engine": report["engine"]["name"],
        "sample_page_count": report["actual"]["sample_page_count"],
        "failed_page_count": report["actual"]["failed_page_count"],
        "native_text_page_count": report["actual"]["native_text_page_count"],
        "scanned_or_derived_page_count": report["actual"]["scanned_or_derived_page_count"],
        "mean_similarity_to_registered_processing_text": round(sum(values) / len(values), 6),
        "pages_below_similarity_0_97": sum(value < 0.97 for value in values),
        "comparison_status": report["status"],
    }


def decide() -> dict:
    input_fingerprint, _ = ocr_fingerprint()
    rapid_path = find_matching_artifact("stage5_rapidocr_sample_benchmark_*.json", input_fingerprint)
    if rapid_path is None:
        raise FileNotFoundError("no RapidOCR sample artifact matches the current input fingerprint")
    rapid = json.loads(rapid_path.read_text(encoding="utf-8"))
    rapid_summary = _summary(rapid, "fresh_vs_registered_similarity")
    return {
        "schema_version": 1,
        "stage": "5",
        "artifact_kind": "stage5_engine_decision",
        "decided_at": TODAY,
        "ocr_input_fingerprint": input_fingerprint,
        "ocr_artifact": str(rapid_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "selection": {
            "primary_engine": "rapidocr_onnxruntime",
            "primary_use": "扫描件首轮 OCR 与页面基线生成",
            "fallback_policy": "低置信度或关键页只回到 Original materials 原始页人工复核，不切换 OCR 引擎",
        },
        "comparison": {
            "rapidocr": rapid_summary,
        },
        "decision_basis": [
            "RapidOCR 在项目专用运行环境完成冻结 Golden Sample 的 36 页复跑，0 失败。",
            "以后所有 OCR 结果只使用 RapidOCR；低置信度和关键页回到 Original materials 原始页复核，不使用第二套 OCR 结果覆盖原件。",
            "相似度只用于发现疑点，不是 OCR 字符、数字、单位、否定词或表格准确率证明。",
        ],
        "quality_boundary": {
            "original_pdf_is_authority": True,
            "unresolved_visual_or_table_content_is_quarantined": True,
            "formal_evidence_generation": "not_stage5_output",
        },
        "status": "engine_choice_recorded",
    }


def main() -> int:
    output = STAGE5_ROOT / f"stage5_engine_decision_{TODAY}.json"
    result = decide()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
