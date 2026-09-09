"""Record the reproducible Stage 5 OCR engine choice from both sample runs."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from turbine_kg.settings import PROJECT_ROOT


STAGE5_ROOT = PROJECT_ROOT / "data" / "stage5"
TODAY = date.today().isoformat()


def _read(name: str) -> dict:
    return json.loads((STAGE5_ROOT / name).read_text(encoding="utf-8"))


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
    rapid = _read(f"stage5_rapidocr_sample_benchmark_{TODAY}.json")
    easy = _read(f"stage5_easyocr_sample_benchmark_{TODAY}.json")
    rapid_summary = _summary(rapid, "fresh_vs_registered_similarity")
    easy_summary = _summary(easy, "fresh_vs_registered_similarity")
    return {
        "schema_version": 1,
        "stage": "5",
        "artifact_kind": "stage5_engine_decision",
        "decided_at": TODAY,
        "selection": {
            "primary_engine": "rapidocr_onnxruntime",
            "backup_engine": "easyocr",
            "primary_use": "扫描件首轮 OCR 与页面基线生成",
            "backup_use": "低置信度、关键页或双引擎不一致页面的交叉复核",
        },
        "comparison": {
            "rapidocr": rapid_summary,
            "easyocr": easy_summary,
        },
        "decision_basis": [
            "两套引擎均在同一项目专用运行环境完成相同的 36 页样本，均为 36/36 页、0 失败。",
            "RapidOCR 对已登记处理文本的样本平均相似度更高，低于 0.97 的页面更少，因此作为首轮主引擎；EasyOCR 保留为备用交叉复核。",
            "相似度只用于工程选型和发现疑点，不是 OCR 字符、数字、单位、否定词或表格准确率证明。",
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
