import pytest

from build_stage5_truth_annotations import _reviewed_transcription


def test_visual_transcription_rejects_stale_correction_or_review():
    original = {"sha256": "original-current"}
    processed = {"sha256": "corrected-current"}
    full_review = {
        "original_sha256": original["sha256"], "processing_sha256": processed["sha256"],
        "line_by_line_reviewed": True, "table_cells_reviewed": True,
        "reading_order_reviewed": True, "reviewed_page_ranges": [[1, 2]],
    }
    visual = {
        "physical_page": 2, "reviewer_kind": "codex_visual_source_review",
        "independent_reference_text": "Actual source transcription",
        "reviewed_transcription_binding": {
            "original_sha256": original["sha256"], "processing_sha256": processed["sha256"],
            "full_review_report_sha256": "review-current",
        },
    }
    gate = {"status": "complete", "next_stage_allowed": True}
    assert _reviewed_transcription(visual, original, processed, full_review,
                                  "review-current", gate) == "Actual source transcription"
    with pytest.raises(ValueError):
        _reviewed_transcription(visual, original, {"sha256": "uncorrected-old"},
                                full_review, "review-current", gate)
    with pytest.raises(ValueError):
        _reviewed_transcription(visual, original, processed, full_review,
                                "review-superseded", gate)
    with pytest.raises(ValueError):
        _reviewed_transcription(visual, original, processed, full_review,
                                "review-current", {"status": "blocked"})


def test_plain_generated_candidate_does_not_become_visual_transcription():
    assert _reviewed_transcription({"independent_reference_text": "Generated candidate"},
                                   {}, {}, {}, "", {}) is None


def test_original_visual_transcription_can_bind_user_accepted_current_pdf():
    original = {"sha256": "original-current"}
    processed = {"sha256": "corrected-current"}
    full_review = {
        "original_sha256": original["sha256"],
        "processing_sha256": processed["sha256"],
        "line_by_line_reviewed": False,
        "table_cells_reviewed": False,
        "reading_order_reviewed": False,
        "reviewed_page_ranges": [[1, 2]],
        "user_acceptance": {
            "accepted": True,
            "acceptance_kind": "user_confirmation_of_current_ocr_delivery",
            "confirmation_text": "当前资料可以通过",
            "original_sha256": original["sha256"],
            "processing_sha256": processed["sha256"],
            "accepted_page_ranges": [[1, 2]],
            "does_not_assert_agent_line_by_line_or_cell_review": True,
        },
    }
    visual = {
        "document_key": "book", "physical_page": 2,
        "reviewer_kind": "codex_visual_source_review",
        "independent_reference_text": "Earlier visual transcription from original",
        "reviewed_transcription_binding": {
            "original_sha256": original["sha256"],
            "processing_sha256": processed["sha256"],
            "full_review_report_sha256": "report-current",
        },
    }
    gate = {
        "status": "complete", "next_stage_allowed": True,
        "documents": [{"document_key": "book", "expected_pages": 2,
                       "review_basis": "explicit_current_pdf_user_acceptance"}],
    }
    assert _reviewed_transcription(
        visual, original, processed, full_review, "report-current", gate
    ) == "Earlier visual transcription from original"
    full_review["user_acceptance"]["processing_sha256"] = "historical"
    with pytest.raises(ValueError):
        _reviewed_transcription(visual, original, processed, full_review,
                                "report-current", gate)
