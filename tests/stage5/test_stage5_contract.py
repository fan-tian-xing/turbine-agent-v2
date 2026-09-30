"""Stage 5 full-corpus exit gate regression tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from shutil import copyfile
from types import SimpleNamespace

import pymupdf
import pytest

import audit_stage5_exit as gate


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pdf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = pymupdf.open()
    page = pdf.new_page(width=240, height=180)
    page.insert_text((20, 40), text)
    pdf.save(path)
    pdf.close()


@pytest.fixture()
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    keys = ["scan_a", "scan_b", "scan_c", "native_copy", "native_only"]
    monkeypatch.setattr(gate, "EXPECTED_DOCUMENTS", 5)
    monkeypatch.setattr(gate, "EXPECTED_PAGES", 5)
    monkeypatch.setattr(gate, "EXPECTED_DERIVED", 4)
    monkeypatch.setattr(gate, "EXPECTED_SCANNED_PAGES", 3)
    monkeypatch.setattr(gate, "EXPECTED_NATIVE_PAGES", 2)
    source = tmp_path / "source"
    derived = tmp_path / "derived"
    documents = []
    assets = []
    reviews = []
    paths = {}
    for index, key in enumerate(keys):
        source_relative = f"{key}.pdf"
        original = source / source_relative
        _pdf(original, f"visible page {index}")
        original_asset = f"original-{key}"
        assets.append({
            "asset_id": original_asset,
            "source_root_id": "source",
            "relative_path": source_relative,
            "sha256": _sha(original),
        })
        if index < 4:
            processed_relative = f"OCR/{key}(OCR).pdf"
            processed = derived / f"{key}(OCR).pdf"
            processed.parent.mkdir(parents=True, exist_ok=True)
            copyfile(original, processed)
            processed_asset = f"processed-{key}"
            assets.append({
                "asset_id": processed_asset,
                "source_root_id": "ocr_derived",
                "relative_path": processed_relative,
                "sha256": _sha(processed),
            })
        else:
            processed_relative = source_relative
            processed = original
            processed_asset = original_asset
        documents.append({
            "document_key": key,
            "original_asset_id": original_asset,
            "processing_asset_id": processed_asset,
            "original_relative_path": source_relative,
            "processing_relative_path": processed_relative,
            "page_count": 1,
        })
        reviews.append({
            "document_key": key,
            "original_sha256": _sha(original),
            "processing_sha256": _sha(processed),
            "reviewed_page_ranges": [[1, 1]],
            "line_by_line_reviewed": True,
            "table_cells_reviewed": True,
            "reading_order_reviewed": True,
            "blank_pages": [],
            "source_unreadable_pages": [],
            "unresolved_text_count": 0,
            "unresolved_table_cell_count": 0,
            "unresolved_page_mapping_count": 0,
        })
        paths[key] = (original, processed)
    manifest = {
        "documents": documents,
        "full_page_processing": {
            "total_existing_physical_pages": 5,
            "scanned_page_count": 3,
            "native_text_page_count": 2,
            "scanned_documents": keys[:3],
            "native_text_documents": keys[3:],
        },
    }
    manifest_path = tmp_path / "manifest.json"
    registry_path = tmp_path / "assets.jsonl"
    review_path = tmp_path / "review.json"
    state = {
        "manifest": manifest,
        "assets": assets,
        "reviews": reviews,
        "manifest_path": manifest_path,
        "registry_path": registry_path,
        "review_path": review_path,
        "settings": SimpleNamespace(source_root=source, ocr_derived_root=derived),
        "paths": paths,
    }
    _save(state)
    return state


def _save(state: dict) -> None:
    state["manifest_path"].write_text(json.dumps(state["manifest"]), encoding="utf-8")
    state["registry_path"].write_text(
        "\n".join(json.dumps(row) for row in state["assets"]) + "\n",
        encoding="utf-8",
    )
    state["review_path"].write_text(
        json.dumps({"full_corpus_reviews": state["reviews"]}),
        encoding="utf-8",
    )


def _audit(state: dict) -> dict:
    return gate.audit(
        state["manifest_path"],
        state["registry_path"],
        state["review_path"],
        state["settings"],
    )


def test_complete_current_five_document_review_can_pass(corpus: dict) -> None:
    result = _audit(corpus)
    assert result["status"] == "complete"
    assert result["next_stage_allowed"] is True
    assert result["blocking_items"] == []


def test_missing_derived_pdf_blocks(corpus: dict) -> None:
    corpus["paths"]["scan_b"][1].unlink()
    result = _audit(corpus)
    assert result["status"] == "blocked"
    assert any("processed PDF missing" in issue for issue in result["blocking_items"])


def test_wrong_visible_page_blocks_even_when_hashes_updated(corpus: dict) -> None:
    processed = corpus["paths"]["scan_a"][1]
    replacement = processed.with_suffix(".new.pdf")
    _pdf(replacement, "different visible page")
    replacement.replace(processed)
    current_sha = _sha(processed)
    next(row for row in corpus["assets"] if row["asset_id"] == "processed-scan_a")["sha256"] = current_sha
    next(row for row in corpus["reviews"] if row["document_key"] == "scan_a")["processing_sha256"] = current_sha
    _save(corpus)
    result = _audit(corpus)
    assert result["status"] == "blocked"
    assert result["documents"][0]["pixel_mismatch_pages"] == [1]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda row: row.update(reviewed_page_ranges=[]), "does not cover every physical page"),
        (lambda row: row.update(processing_sha256="old"), "processed SHA-256 is stale"),
        (lambda row: row.update(unresolved_table_cell_count=1), "unresolved_table_cell_count is not zero"),
        (lambda row: row.update(reading_order_reviewed=False), "reading-order review not confirmed"),
    ],
)
def test_incomplete_or_stale_manual_review_blocks(
    corpus: dict, change, message: str
) -> None:
    change(corpus["reviews"][0])
    _save(corpus)
    result = _audit(corpus)
    assert result["status"] == "blocked"
    assert any(message in issue for issue in result["blocking_items"])


def test_historical_sample_review_does_not_count(corpus: dict) -> None:
    corpus["review_path"].write_text(
        json.dumps({"reviews": [{"document_key": "scan_a", "comparison_status": "passed"}]}),
        encoding="utf-8",
    )
    result = _audit(corpus)
    assert result["status"] == "blocked"
    assert any("full_corpus_reviews missing" in issue for issue in result["blocking_items"])


def test_explicit_user_acceptance_of_exact_pdf_can_close_review_gate(corpus: dict) -> None:
    review = corpus["reviews"][0]
    review.update(
        line_by_line_reviewed=False,
        table_cells_reviewed=False,
        reading_order_reviewed=False,
        unresolved_text_count=None,
        unresolved_table_cell_count=None,
        unresolved_page_mapping_count=None,
        user_acceptance={
            "accepted": True,
            "acceptance_kind": "user_confirmation_of_current_ocr_delivery",
            "confirmation_text": "我确认当前资料可以通过",
            "original_sha256": review["original_sha256"],
            "processing_sha256": review["processing_sha256"],
            "accepted_page_ranges": [[1, 1]],
            "does_not_assert_agent_line_by_line_or_cell_review": True,
        },
    )
    _save(corpus)
    result = _audit(corpus)
    assert result["status"] == "complete"
    assert result["documents"][0]["review_basis"] == "explicit_current_pdf_user_acceptance"
    assert review["line_by_line_reviewed"] is False


@pytest.mark.parametrize("invalid", ["old_sha", "partial_pages", "false_method_claim"])
def test_user_acceptance_must_bind_exact_full_pdf(corpus: dict, invalid: str) -> None:
    review = corpus["reviews"][0]
    review["line_by_line_reviewed"] = False
    review["user_acceptance"] = {
        "accepted": True,
        "acceptance_kind": "user_confirmation_of_current_ocr_delivery",
        "confirmation_text": "我确认当前资料可以通过",
        "original_sha256": review["original_sha256"],
        "processing_sha256": review["processing_sha256"],
        "accepted_page_ranges": [[1, 1]],
        "does_not_assert_agent_line_by_line_or_cell_review": True,
    }
    if invalid == "old_sha":
        review["user_acceptance"]["processing_sha256"] = "historical"
    elif invalid == "partial_pages":
        review["user_acceptance"]["accepted_page_ranges"] = []
    else:
        review["user_acceptance"]["does_not_assert_agent_line_by_line_or_cell_review"] = False
    _save(corpus)
    result = _audit(corpus)
    assert result["status"] == "blocked"
    assert any("user acceptance is not bound" in item for item in result["blocking_items"])


def test_unsearchable_nonblank_page_blocks(corpus: dict) -> None:
    original, processed = corpus["paths"]["scan_a"]
    blank = processed.with_suffix(".new.pdf")
    pdf = pymupdf.open()
    page = pdf.new_page(width=240, height=180)
    page.draw_rect(pymupdf.Rect(20, 30, 180, 70), fill=(0, 0, 0))
    pdf.save(blank)
    pdf.close()
    blank.replace(processed)
    sha = _sha(processed)
    next(row for row in corpus["assets"] if row["asset_id"] == "processed-scan_a")["sha256"] = sha
    next(row for row in corpus["reviews"] if row["document_key"] == "scan_a")["processing_sha256"] = sha
    _save(corpus)
    result = _audit(corpus)
    assert result["status"] == "blocked"
    assert result["documents"][0]["unsearchable_pages"] == [1]
