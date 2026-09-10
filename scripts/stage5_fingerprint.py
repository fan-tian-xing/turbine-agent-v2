"""Stable fingerprints for reusable Stage 5 artifacts."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

from turbine_kg.settings import PROJECT_ROOT, Settings


STAGE5_ROOT = PROJECT_ROOT / "data" / "stage5"
SAMPLE_MANIFEST = STAGE5_ROOT / "stage5_sample_manifest.json"
REGISTRY_ASSETS = PROJECT_ROOT / "data" / "registry" / "source_assets.jsonl"
OCR_DPI = 170


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_assets() -> dict[str, dict]:
    return {
        row["asset_id"]: row
        for line in REGISTRY_ASSETS.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for row in (json.loads(line),)
    }


def resolve_asset(asset: dict, settings: Settings) -> Path:
    if asset["source_root_id"] == "source":
        return settings.source_root / asset["relative_path"]
    if asset["source_root_id"] == "ocr_derived" and asset["relative_path"].startswith("OCR/"):
        return settings.ocr_derived_root / asset["relative_path"].removeprefix("OCR/")
    raise ValueError(f"unsupported asset root/path: {asset['asset_id']}")


def _canonical_hash(value: dict) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def ocr_fingerprint() -> tuple[str, dict]:
    """Return the identity of the inputs that make a RapidOCR result reusable."""

    settings = Settings.from_environment()
    manifest = json.loads(SAMPLE_MANIFEST.read_text(encoding="utf-8"))
    assets = load_assets()
    selected_assets = []
    for document in manifest["documents"]:
        for key in ("original_asset_id", "processing_asset_id"):
            asset = assets[document[key]]
            path = resolve_asset(asset, settings)
            selected_assets.append(
                {
                    "asset_id": asset["asset_id"],
                    "relative_path": asset["relative_path"],
                    "sha256": sha256_file(path),
                }
            )
    script_paths = [
        PROJECT_ROOT / "scripts" / "benchmark_stage5_rapidocr_sample.py",
        PROJECT_ROOT / "scripts" / "generate_ocr_pdf.py",
        PROJECT_ROOT / "scripts" / "stage5_fingerprint.py",
        PROJECT_ROOT / "pyproject.toml",
        PROJECT_ROOT / "uv.lock",
    ]
    components = {
        "manifest_sha256": sha256_file(SAMPLE_MANIFEST),
        "selected_assets": selected_assets,
        "ocr_engine": "rapidocr_onnxruntime",
        "ocr_engine_version": importlib.metadata.version("rapidocr-onnxruntime"),
        "render_dpi": OCR_DPI,
        "python_version": ".".join(str(item) for item in sys.version_info[:3]),
        "script_sha256": {
            str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"): sha256_file(path)
            for path in script_paths
        },
    }
    return _canonical_hash(components), components


def find_matching_artifact(pattern: str, fingerprint: str) -> Path | None:
    for path in sorted(STAGE5_ROOT.glob(pattern), reverse=True):
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if artifact.get("input_fingerprint") == fingerprint:
            return path
    return None
