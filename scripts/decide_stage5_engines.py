"""Record the reproducible Stage 5 OCR engine choice."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from turbine_kg.settings import PROJECT_ROOT
from benchmark_stage5_quality import BASELINE, TRUTH, _current_inputs
from stage5_fingerprint import sha256_file
from turbine_kg.settings import Settings


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
    manifest, _, baseline, truth, input_fingerprint, components = _current_inputs(Settings.from_environment())
    return {
        "schema_version": 1,
        "stage": "5",
        "artifact_kind": "stage5_engine_decision",
        "decided_at": TODAY,
        "ocr_input_fingerprint": input_fingerprint,
        "fingerprint_components": components,
        "baseline": str(BASELINE.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "truth_annotations": str(TRUTH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "inputs": {"baseline_sha256": sha256_file(BASELINE), "truth_annotations_sha256": sha256_file(TRUTH)},
        "selection": {
            "primary_engine": "rapidocr_onnxruntime",
            "primary_use": "扫描页首轮候选识别；后续处理读取已对原件逐页纠正的当前正式PDF",
            "current_processing_source": "current_registered_reviewed_pdf",
            "fallback_policy": "复核疑点时可用第二识别器辅助，最终文字仍由原件逐页复核确认",
        },
        "comparison": {
            "current_baseline": {"document_count": len(manifest["documents"]),
                                 "page_count": baseline["actual"]["page_count"],
                                 "failed_page_count": baseline["actual"]["failed_page_count"],
                                 "truth_sample_page_count": len(truth["records"]),
                                 "comparison_status": "current_pdf_identity_and_coverage_checked"},
        },
        "decision_basis": [
            "当前基线和独立原页转录绑定当前正式PDF、Registry和全文复核记录；旧RapidOCR样本输出不再作为正式输入。",
            "首轮候选可使用RapidOCR、版面和表格模型，复核疑点时可用第二识别器；最终校正在派生PDF文字层中保留，原件只读。",
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
