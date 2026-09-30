"""Keep current OCR bytes separate from the frozen historical batch identity."""

from copy import deepcopy

import pytest

from audit_stage4_full_parse import _current_manifest_items
from turbine_kg.documents.catalog import AssetIdentity, IdentityCatalog


@pytest.fixture
def current_batch():
    assets, records, documents, historical, reviews = [], {}, [], [], []
    for index in range(5):
        document_id, revision_id = f"doc-{index:020x}", f"rev-{index + 5:020x}"
        original_id, processing_id = f"asset-{index + 10:020x}", f"asset-{index + 15:020x}"
        original = AssetIdentity(original_id, document_id, revision_id, "original", f"source/{index}.pdf", "b" * 64, "source")
        processing = AssetIdentity(processing_id, document_id, revision_id, "derived_ocr", f"OCR/{index}.pdf", "c" * 64, "ocr_derived", original_id, "ocr_derivative")
        assets.extend((original, processing))
        for asset in (original, processing):
            records[asset.asset_id] = {"asset_id": asset.asset_id, "document_logical_id": document_id,
                                      "revision_id": revision_id, "relative_path": asset.relative_path,
                                      "sha256": asset.sha256, "page_count": 155}
        key = f"source-{index}"
        documents.append({"document_key": key, "document_logical_id": document_id,
                          "original_asset_id": original_id, "processing_asset_id": processing_id,
                          "original_relative_path": original.relative_path, "processing_relative_path": processing.relative_path,
                          "page_count": 155})
        # The first historical source was native; all current sources use OCR.
        historical.append({"document_logical_id": document_id, "revision_id": revision_id,
                           "asset_id": original_id if index == 0 else processing_id, "sha256": "a" * 64})
        reviews.append({"document_key": key, "original_sha256": original.sha256, "processing_sha256": processing.sha256,
                        "line_by_line_reviewed": True, "table_cells_reviewed": True, "reading_order_reviewed": True,
                        "unresolved_text_count": 0, "unresolved_table_cell_count": 0, "unresolved_page_mapping_count": 0,
                        "reviewed_page_ranges": [[1, 155]]})
    return ({"artifact_kind": "stage5_sample_manifest", "status": "complete", "documents": documents},
            {"status": "frozen", "manifest_kind": "research_trial_sampling", "source_documents": historical},
            {"full_corpus_reviews": reviews}, records, IdentityCatalog(tuple(assets), (), {}))


def test_current_batch_uses_new_processing_hashes_without_rewriting_history(current_batch):
    manifest, frozen, reviews, records, catalog = current_batch
    before = deepcopy(frozen)
    items = _current_manifest_items(manifest, frozen, reviews, records, catalog)
    assert len(items) == 5
    assert all(item["sha256"] == "c" * 64 for item in items)
    assert items[0]["asset_id"] == manifest["documents"][0]["processing_asset_id"]
    assert frozen == before


@pytest.mark.parametrize("change", ["missing_document", "duplicate_document", "original", "revision", "processing_path"])
def test_current_selection_cannot_change_frozen_scope_or_original_identity(current_batch, change):
    manifest, frozen, reviews, records, catalog = current_batch
    if change == "missing_document": manifest["documents"].pop()
    elif change == "duplicate_document": manifest["documents"][0] = deepcopy(manifest["documents"][1])
    elif change == "original": manifest["documents"][0]["original_asset_id"] = manifest["documents"][1]["original_asset_id"]
    elif change == "revision": frozen["source_documents"][0]["revision_id"] = "rev-" + "f" * 20
    else: manifest["documents"][0]["processing_relative_path"] = "OCR/not-the-selected-file.pdf"
    with pytest.raises(ValueError):
        _current_manifest_items(manifest, frozen, reviews, records, catalog)


@pytest.mark.parametrize("change", ["processing_hash", "original_hash", "duplicate_review"])
def test_stage4_review_identity_must_bind_exact_current_bytes(current_batch, change):
    manifest, frozen, reviews, records, catalog = current_batch
    row = reviews["full_corpus_reviews"][0]
    if change == "processing_hash": row["processing_sha256"] = "a" * 64
    elif change == "original_hash": row["original_sha256"] = "a" * 64
    elif change == "duplicate_review": reviews["full_corpus_reviews"].append(deepcopy(row))
    with pytest.raises(ValueError):
        _current_manifest_items(manifest, frozen, reviews, records, catalog)


def test_stage4_structural_parse_does_not_claim_stage5_review_completion(current_batch):
    manifest, frozen, reviews, records, catalog = current_batch
    row = reviews["full_corpus_reviews"][0]
    row.update(line_by_line_reviewed=False, table_cells_reviewed=False,
               reading_order_reviewed=False, unresolved_text_count=None,
               unresolved_table_cell_count=None, unresolved_page_mapping_count=None,
               reviewed_page_ranges=[])
    assert len(_current_manifest_items(manifest, frozen, reviews, records, catalog)) == 5
