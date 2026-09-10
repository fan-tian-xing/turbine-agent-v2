"""Verify that the project runtime contains and can execute required OCR/PDF libraries."""

from __future__ import annotations

from datetime import date
import importlib
import importlib.metadata
import json
from pathlib import Path
import sys

import numpy as np
import pymupdf

from turbine_kg.settings import PROJECT_ROOT, Settings


TODAY = date.today().isoformat()
OUTPUT = PROJECT_ROOT / "data" / "stage5" / f"stage5_runtime_environment_audit_{TODAY}.json"
REQUIRED_MODULES = {
    "neo4j": "neo4j",
    "numpy": "numpy",
    "opencv": "cv2",
    "pymupdf": "pymupdf",
    "fitz_compat": "fitz",
    "rapidocr": "rapidocr_onnxruntime",
    "onnxruntime": "onnxruntime",
    "pytest": "pytest",
}
PACKAGE_NAMES = {
    "neo4j": "neo4j",
    "numpy": "numpy",
    "opencv-python": "opencv-python",
    "pymupdf": "PyMuPDF",
    "rapidocr-onnxruntime": "rapidocr_onnxruntime",
    "onnxruntime": "onnxruntime",
    "pytest": "pytest",
}


def module_status() -> dict[str, bool]:
    result = {}
    for label, module_name in REQUIRED_MODULES.items():
        try:
            importlib.import_module(module_name)
            result[label] = True
        except Exception:
            result[label] = False
    return result


def package_versions() -> dict[str, str | None]:
    versions = {}
    for label, package_name in PACKAGE_NAMES.items():
        try:
            versions[label] = importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            versions[label] = None
    return versions


def engine_smoke() -> dict:
    import cv2
    from rapidocr_onnxruntime import RapidOCR

    settings = Settings.from_environment()
    source = settings.source_root / "2.书籍" / "260824 扫描文件" / "汽轮机辅机安装（第二版）.pdf"
    physical_page = 300
    document = pymupdf.open(source)
    page = document[physical_page - 1]
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
    rapid_result, _ = RapidOCR()(image)
    document.close()
    return {
        "source": str(source.relative_to(PROJECT_ROOT.parent)).replace("\\", "/"),
        "physical_pdf_page": physical_page,
        "logical_page_label": "291",
        "rapidocr_item_count": len(rapid_result or []),
        "status": "pass" if rapid_result else "fail",
    }


def audit() -> dict:
    modules = module_status()
    smoke = engine_smoke()
    return {
        "schema_version": 1,
        "stage": "5",
        "artifact_kind": "stage5_runtime_environment_audit",
        "audited_at": TODAY,
        "status": "pass" if all(modules.values()) and smoke["status"] == "pass" else "fail",
        "runtime": {
            "python_executable": sys.executable,
            "python_version": sys.version,
            "prefix": sys.prefix,
            "base_prefix": sys.base_prefix,
            "is_virtual_environment": sys.prefix != sys.base_prefix,
            "required_installation_target": "D:/本体/汽轮机安调项目/项目初期demo/runtime-python/turbine-kg-env",
            "target_matches_runtime": str(Path(sys.prefix).resolve()).replace("\\", "/").endswith("runtime-python/turbine-kg-env"),
        },
        "module_status": modules,
        "package_versions": package_versions(),
        "engine_smoke": smoke,
        "boundaries": [
            "This audit must be run with runtime-python/turbine-kg-env/Scripts/python.exe.",
            "The smoke test proves RapidOCR executes; it does not establish OCR accuracy.",
            "Accuracy remains governed by original-page comparison and Stage 5 quality gates.",
        ],
    }


def main() -> int:
    result = audit()
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(OUTPUT), "python": result["runtime"]["python_executable"]}, ensure_ascii=False))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
