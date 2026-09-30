from turbine_kg.documents.ocr_review import vertical_margin_text_runs


def test_vertical_margin_navigation_is_flagged_for_source_review():
    boxes = [
        {"text": "选择题正文", "x0": 100, "x1": 600, "y0": 100, "y1": 125},
        {"text": "选", "x0": 860, "x1": 885, "y0": 140, "y1": 165},
        {"text": "择", "x0": 860, "x1": 885, "y0": 175, "y1": 200},
        {"text": "题", "x0": 860, "x1": 885, "y0": 210, "y1": 235},
        {"text": "表", "x0": 500, "x1": 525, "y0": 140, "y1": 165},
        {"text": "格", "x0": 500, "x1": 525, "y0": 175, "y1": 200},
        {"text": "值", "x0": 500, "x1": 525, "y0": 210, "y1": 235},
    ]
    assert vertical_margin_text_runs(boxes, page_width_px=930) == ((1, 2, 3),)


def test_isolated_margin_character_does_not_trigger_run():
    boxes = [
        {"text": "项", "x0": 860, "x1": 885, "y0": 140, "y1": 165},
        {"text": "目", "x0": 860, "x1": 885, "y0": 175, "y1": 200},
        {"text": "正常正文", "x0": 100, "x1": 600, "y0": 210, "y1": 235},
    ]
    assert vertical_margin_text_runs(boxes, page_width_px=930) == ()
