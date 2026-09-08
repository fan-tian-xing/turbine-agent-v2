"""Build the non-authoritative 15-page Stage 3 real-source trial sample."""

from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover - compatibility with older local runtimes
    import fitz  # type: ignore[no-redef]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from turbine_kg.settings import Settings


REGISTRY_ROOT = PROJECT_ROOT / "data" / "registry"
STAGE3_ROOT = PROJECT_ROOT / "data" / "stage3"
MANIFEST_PATH = STAGE3_ROOT / "initial_batch_manifest.json"
SUMMARY_PATH = STAGE3_ROOT / "real_trial_summary.json"
CONFIRMATION_PATH = STAGE3_ROOT / "real_trial_confirmation.json"
RUNTIME_ROOT = PROJECT_ROOT / "var" / "stage3"
RUNTIME_CORPUS_PATH = RUNTIME_ROOT / "real_trial_pages.json"


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _resolve_asset(asset: dict[str, object], settings: Settings) -> Path:
    relative_path = str(asset["relative_path"])
    root = settings.source_root if asset["source_root_id"] == "source" else settings.ocr_derived_root
    if relative_path.startswith("OCR/"):
        relative_path = relative_path.removeprefix("OCR/")
    return root / Path(*relative_path.split("/"))


def _asset_for_document(selected: dict[str, object], assets: list[dict[str, object]], settings: Settings) -> tuple[dict[str, object], Path]:
    document_id = str(selected["document_logical_id"])
    frozen_asset_id = selected.get("asset_id")
    if frozen_asset_id:
        candidates = [asset for asset in assets if asset["asset_id"] == frozen_asset_id]
        if len(candidates) != 1:
            raise ValueError(f"frozen manifest asset is missing or duplicated: {frozen_asset_id}")
        asset = candidates[0]
        for field in ("document_logical_id", "revision_id", "sha256", "relative_path"):
            if asset[field] != selected[field]:
                raise ValueError(f"frozen manifest mismatch for {frozen_asset_id}: {field}")
        path = _resolve_asset(asset, settings)
        if not path.is_file():
            raise FileNotFoundError(f"frozen manifest asset is not available: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != asset["sha256"]:
            raise ValueError(f"frozen manifest asset bytes do not match Registry SHA-256: {path}")
        return asset, path
    candidates = [
        asset
        for asset in assets
        if asset["document_logical_id"] == document_id
        and asset["text_adapter_status"] in {"native_text_available", "ocr_validated"}
        and asset["admission_status"] in {"admitted", "duplicate_or_derivative"}
    ]
    candidates.sort(key=lambda asset: (asset["text_layer_status"] != "native_text", asset["asset_kind"] != "derived_ocr", asset["relative_path"]))
    for asset in candidates:
        path = _resolve_asset(asset, settings)
        if path.is_file():
            return asset, path
    raise FileNotFoundError(f"no usable admitted asset found for {document_id}")


def build() -> dict[str, object]:
    settings = Settings.from_environment()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest["status"] != "frozen":
        raise ValueError("initial_batch_manifest must be frozen before building the real trial")
    assets = _load_jsonl(REGISTRY_ROOT / "source_assets.jsonl")
    documents = _load_jsonl(REGISTRY_ROOT / "source_documents.jsonl")
    source_roles = {item["document_logical_id"]: item["source_roles"][0] for item in documents}
    pages: list[dict[str, object]] = []
    selected_assets: list[dict[str, object]] = []
    for selected in manifest["source_documents"]:
        document_id = str(selected["document_logical_id"])
        asset, pdf_path = _asset_for_document(selected, assets, settings)
        selected_assets.append(asset)
        with fitz.open(pdf_path) as pdf:
            for page_number in selected["sample_pdf_pages"]:
                page = pdf[int(page_number) - 1]
                text = page.get_text("text")
                normalized = " ".join(text.split())
                if not normalized:
                    raise ValueError(f"selected page has no usable text: {document_id} PDF page {page_number}")
                page_id = f"real-page-{document_id[4:10]}-{int(page_number):03d}"
                span_id = f"real-span-{document_id[4:10]}-{int(page_number):03d}"
                pages.append(
                    {
                        "page_id": page_id,
                        "span_id": span_id,
                        "document_logical_id": document_id,
                        "revision_id": asset["revision_id"],
                        "asset_id": asset["asset_id"],
                        "asset_sha256": asset["sha256"],
                        "relative_path": asset["relative_path"],
                        "source_role": source_roles[document_id],
                        "pdf_page_number": int(page_number),
                        "text_character_count": len(text),
                        "span_preview": normalized[:360],
                        "text_adapter_status": asset["text_adapter_status"],
                    }
                )
    if len(pages) != manifest["page_sample_count"]:
        raise ValueError("real trial page count does not match the frozen manifest")
    confirmation = {}
    if CONFIRMATION_PATH.is_file():
        confirmation = json.loads(CONFIRMATION_PATH.read_text(encoding="utf-8"))
    confirmed = confirmation.get("status") == "confirmed_by_user"
    summary = {
        "schema_version": 1,
        "stage": "3",
        "status": "evidence_confirmed" if confirmed else "extracted_pending_manual_evidence_review",
        "page_sample_count": len(pages),
        "selected_asset_ids": [asset["asset_id"] for asset in selected_assets],
        "selected_document_ids": [item["document_logical_id"] for item in manifest["source_documents"]],
        "text_adapter_status_counts": {
            status: sum(page["text_adapter_status"] == status for page in pages)
            for status in {page["text_adapter_status"] for page in pages}
        },
        "evidence_review": {
            "candidate_count": len(pages),
            "confirmed_count": int(confirmation.get("confirmation_group_count", 0)) if confirmed else 0,
            "status": "confirmed_by_user" if confirmed else "pending_user_confirmation",
            **({"confirmation_artifact": str(CONFIRMATION_PATH.relative_to(PROJECT_ROOT))} if confirmed else {}),
        },
        "runtime_corpus": str(RUNTIME_CORPUS_PATH.relative_to(PROJECT_ROOT)),
    }
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    RUNTIME_CORPUS_PATH.write_text(json.dumps({"schema_version": 1, "pages": pages}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
