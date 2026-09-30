"""Create searchable OCR PDFs using page-region routing and transient layout blocks."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import tempfile

import cv2
import pymupdf as fitz
import numpy as np
from rapidocr_onnxruntime import RapidOCR

from turbine_kg.settings import Settings


@dataclass
class Block:
    """Short-lived normalized page region; never serialized to a sidecar."""

    kind: str
    bbox_px: tuple[float, float, float, float]
    score: float
    reading_order: int | None
    engine: str
    boxes: list[dict] = field(default_factory=list)
    cells: list[dict] = field(default_factory=list)
    label: str | None = None
    status: str = "candidate"
    source_page: int | None = None


class _TableGridParser(HTMLParser):
    """Read Paddle's HTML-like table structure while retaining cell spans."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict]] = []
        self.row = -1
        self.cells: list[dict] = []
        self.occupied: dict[tuple[int, int], int] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row += 1
            self.rows.append([])
        elif tag in {"td", "th"}:
            attrs_map = dict(attrs)
            row_span = max(1, int(attrs_map.get("rowspan") or 1))
            column_span = max(1, int(attrs_map.get("colspan") or 1))
            column = 0
            while (self.row, column) in self.occupied:
                column += 1
            cell = {
                "row": self.row,
                "column": column,
                "row_span": row_span,
                "column_span": column_span,
                "header_candidate": tag == "th" or self.row == 0,
                "text": "",
                "bbox_px": None,
                "cell_status": "structure_candidate",
            }
            self.cells.append(cell)
            self.rows[self.row].append(len(self.cells) - 1)
            for r in range(self.row, self.row + row_span):
                for c in range(column, column + column_span):
                    self.occupied[r, c] = len(self.cells) - 1


def _box_record(box: list, text: str, score: float, source_index: int) -> dict:
    points = np.asarray(box, dtype=float).reshape(-1, 2)
    return {
        "text": str(text),
        "score": float(score),
        "source_index": source_index,
        "bbox_px": {
            "x0": float(points[:, 0].min()),
            "y0": float(points[:, 1].min()),
            "x1": float(points[:, 0].max()),
            "y1": float(points[:, 1].max()),
        },
    }


def _same_line(left: dict, right: dict) -> bool:
    a, b = left["bbox_px"], right["bbox_px"]
    overlap = max(0.0, min(a["y1"], b["y1"]) - max(a["y0"], b["y0"]))
    min_height = max(1.0, min(a["y1"] - a["y0"], b["y1"] - b["y0"]))
    center_delta = abs((a["y0"] + a["y1"] - b["y0"] - b["y1"]) / 2)
    return overlap / min_height >= 0.35 or center_delta <= min_height * 0.3


def _group_lines(boxes: list[dict]) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for box in sorted(boxes, key=lambda item: (
        (item["bbox_px"]["y0"] + item["bbox_px"]["y1"]) / 2,
        item["bbox_px"]["x0"],
    )):
        candidates = [line for line in lines if any(_same_line(box, item) for item in line)]
        if candidates:
            candidates[-1].append(box)
        else:
            lines.append([box])
    for line in lines:
        line.sort(key=lambda item: item["bbox_px"]["x0"])
    lines.sort(key=lambda line: min(item["bbox_px"]["y0"] for item in line))
    return lines


def grouped_text(result: list) -> str:
    """Compatibility helper for the 36-page RapidOCR engine comparison."""
    boxes = [
        _box_record(box, text, float(score), index)
        for index, (box, text, score) in enumerate(result or [])
        if text
    ]
    return "\n".join("".join(item["text"] for item in line) for line in _group_lines(boxes))


def default_font_file() -> Path | None:
    configured = os.environ.get("OCR_FONT_FILE")
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if not candidate.is_file():
            raise FileNotFoundError(f"OCR_FONT_FILE does not exist: {candidate}")
        return candidate
    for candidate in (
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
    ):
        if candidate.is_file():
            return candidate
    return None


def _font_paths(primary: Path | None) -> list[Path]:
    candidates = [
        primary,
        Path(r"C:\Windows\Fonts\cambria.ttc"),
        Path(r"C:\Windows\Fonts\segoeui.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
    ]
    result: list[Path] = []
    for candidate in candidates:
        if candidate and candidate.is_file() and candidate not in result:
            result.append(candidate)
    return result


def validate_output_path(output: Path, ocr_derived_root: Path | None = None) -> Path:
    root = (ocr_derived_root or Settings.from_environment().ocr_derived_root).resolve()
    resolved_output = output.expanduser().resolve()
    try:
        resolved_output.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"OCR output must be inside OCR_DERIVED_ROOT ({root}), got {resolved_output}") from exc
    if resolved_output == root:
        raise ValueError("OCR output must be a file below OCR_DERIVED_ROOT")
    return resolved_output


def _res_payload(result: object) -> dict:
    payload = getattr(result, "json", {})
    if callable(payload):
        payload = payload()
    if isinstance(payload, dict):
        return payload.get("res", payload)
    return {}


def _clip_bbox(values: list[float], width: int, height: int) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = (float(value) for value in values)
    return (
        max(0.0, min(x0, width)), max(0.0, min(y0, height)),
        max(0.0, min(x1, width)), max(0.0, min(y1, height)),
    )


def _bbox_from_polygon(poly: list, offset_x: float, offset_y: float) -> tuple[float, float, float, float]:
    points = np.asarray(poly, dtype=float).reshape(-1, 2)
    return (
        float(points[:, 0].min() + offset_x), float(points[:, 1].min() + offset_y),
        float(points[:, 0].max() + offset_x), float(points[:, 1].max() + offset_y),
    )


def _inside(box: dict, bbox: tuple[float, float, float, float]) -> bool:
    rect = box["bbox_px"]
    cx = (rect["x0"] + rect["x1"]) / 2
    cy = (rect["y0"] + rect["y1"]) / 2
    return bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]


def _table_rule_density(image: np.ndarray) -> tuple[float, float]:
    """Estimate the share of a table crop occupied by long horizontal/vertical rules."""
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    height, width = binary.shape[:2]
    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(12, width // 4), 1),
    )
    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (1, max(12, height // 4)),
    )
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)
    area = max(1, height * width)
    return float(np.count_nonzero(horizontal) / area), float(np.count_nonzero(vertical) / area)


def _is_ruled_table(image: np.ndarray) -> bool:
    """Route visibly ruled tables to the wired-table structure model."""
    horizontal, vertical = _table_rule_density(image)
    return horizontal >= 0.004 and vertical >= 0.004


def _inset_region(
    image: np.ndarray,
    bbox: tuple[float, float, float, float],
    offset_x: float,
    offset_y: float,
    margin_x_ratio: float = 0.06,
    margin_y_ratio: float = 0.10,
) -> tuple[np.ndarray, int, int] | None:
    """Crop inside a cell border, returning its pixels and origin or None if collapsed."""
    x0, y0, x1, y1 = (int(value) for value in bbox)
    margin_x = max(2, int((x1 - x0) * margin_x_ratio))
    margin_y = max(2, int((y1 - y0) * margin_y_ratio))
    left = max(0, min(image.shape[1], x0 - int(offset_x) + margin_x))
    top = max(0, min(image.shape[0], y0 - int(offset_y) + margin_y))
    right = max(0, min(image.shape[1], x1 - int(offset_x) - margin_x))
    bottom = max(0, min(image.shape[0], y1 - int(offset_y) - margin_y))
    if right <= left or bottom <= top:
        return None
    return image[top:bottom, left:right], left, top


_RISK_TEXT_RE = re.compile(r"[0-9A-Za-z±土士≤≥<>~%℃°/()（）\[\]{}—–-]")


def _reread_risk_boxes(
    image: np.ndarray,
    boxes: list[dict],
    text_rec_engine: object,
) -> int:
    """Use a second recognizer on low-confidence and notation-heavy text crops."""
    height, width = image.shape[:2]
    selected: list[dict] = []
    crops: list[np.ndarray] = []
    for box in boxes:
        original = str(box.get("text", "")).strip()
        if not original:
            continue
        has_cjk = re.search(r"[\u3400-\u9fff]", original) is not None
        if (
            float(box.get("score", 0.0)) >= 0.92
            and not _RISK_TEXT_RE.search(original)
            and not has_cjk
            and len(original) > 2
        ):
            continue
        rect = box["bbox_px"]
        pad_x = max(2, round((rect["x1"] - rect["x0"]) * 0.04))
        pad_y = max(2, round((rect["y1"] - rect["y0"]) * 0.12))
        x0 = max(0, int(rect["x0"]) - pad_x)
        y0 = max(0, int(rect["y0"]) - pad_y)
        x1 = min(width, int(rect["x1"]) + pad_x)
        y1 = min(height, int(rect["y1"]) + pad_y)
        crop = image[y0:y1, x0:x1]
        if crop.size and crop.shape[0] >= 5 and crop.shape[1] >= 5:
            crops.append(crop)
            selected.append(box)
    if not crops:
        return 0

    results = text_rec_engine.predict(input=crops)
    changes = 0
    for box, result in zip(selected, results):
        payload = _res_payload(result)
        raw_candidate = str(payload.get("rec_text") or "").strip()
        candidate = raw_candidate
        score = float(payload.get("rec_score") or 0.0)
        original = str(box["text"]).strip()
        if not candidate or score < 0.82 or candidate == original:
            box["text"] = original
            continue
        original_score = float(box.get("score", 0.0))
        # Confidence scales differ slightly across recognizers. Accept a
        # disagreement only when the alternate is not materially weaker.
        if score + 0.02 >= original_score:
            box["text"] = candidate
            box["score"] = score
            box["recognition_engine"] = "paddleocr-pp-ocrv5-server-rec"
            changes += 1
        else:
            box["text"] = original
    return changes


def _reread_table_boxes(
    table_crop: np.ndarray,
    table_bbox: tuple[float, float, float, float],
    boxes: list[dict],
    text_rec_engine: object,
) -> int:
    x0, y0, x1, y1 = table_bbox
    table_boxes = [box for box in boxes if _inside(box, table_bbox)]
    crops: list[np.ndarray] = []
    accepted: list[dict] = []
    for box in table_boxes:
        rect = box["bbox_px"]
        padding = max(2, int((rect["y1"] - rect["y0"]) * 0.12))
        left = max(0, int(rect["x0"] - x0) - padding)
        top = max(0, int(rect["y0"] - y0) - padding)
        right = min(table_crop.shape[1], int(rect["x1"] - x0) + padding)
        bottom = min(table_crop.shape[0], int(rect["y1"] - y0) + padding)
        crop = table_crop[top:bottom, left:right]
        if crop.size and crop.shape[0] >= 5 and crop.shape[1] >= 5:
            crops.append(crop)
            accepted.append(box)
    if not crops:
        return 0
    results = text_rec_engine.predict(input=crops)
    changed = 0
    for box, result in zip(accepted, results):
        payload = _res_payload(result)
        candidate = str(payload.get("rec_text") or "").strip()
        score = float(payload.get("rec_score") or 0.0)
        original = str(box["text"]).strip()
        if candidate and score >= 0.78 and score + 0.02 >= float(box["score"]):
            if candidate != original:
                changed += 1
            box["text"] = candidate
            box["score"] = score
            box["recognition_engine"] = "paddleocr-pp-ocrv5-server-rec"
    return changed


def _table_cells(
    table_crop: np.ndarray,
    table_bbox: tuple[float, float, float, float],
    boxes: list[dict],
    table_engines: dict[str, TableStructureRecognition],
    text_engine: RapidOCR,
    text_rec_engine: object,
    next_source_index: int,
) -> tuple[list[dict], float, int, list[dict], int]:
    x0, y0, _, _ = table_bbox
    table_engine = table_engines["wired"] if _is_ruled_table(table_crop) else table_engines["general"]
    result = table_engine.predict(table_crop)
    payload = _res_payload(result[0]) if result else {}
    parser = _TableGridParser()
    parser.feed("".join(payload.get("structure", [])))
    polygons = payload.get("bbox", [])
    unresolved = 0
    recovered_boxes: list[dict] = []
    reread_changes = _reread_table_boxes(table_crop, table_bbox, boxes, text_rec_engine)
    page_x0, page_y0, _, _ = table_bbox
    for index, cell in enumerate(parser.cells):
        if index >= len(polygons):
            cell["cell_status"] = "structure_bbox_missing"
            unresolved += 1
            continue
        bbox = _bbox_from_polygon(polygons[index], x0, y0)
        cell["bbox_px"] = {"x0": bbox[0], "y0": bbox[1], "x1": bbox[2], "y1": bbox[3]}
        cell_boxes = [box for box in boxes if _inside(box, bbox)]
        cell_boxes.sort(key=lambda item: (item["bbox_px"]["y0"], item["bbox_px"]["x0"]))
        cell["source_indices"] = [box["source_index"] for box in cell_boxes]
        cell["text"] = " ".join(box["text"].strip() for box in cell_boxes if box["text"].strip())
        cell["cell_status"] = "text_detected" if cell["text"] else "empty_candidate"
        weakest_score = min((box["score"] for box in cell_boxes), default=1.0)
        needs_crop_read = not cell["text"] or weakest_score < 0.55
        if needs_crop_read:
            inset = _inset_region(table_crop, bbox, page_x0, page_y0)
            if inset is None:
                if not cell["text"]:
                    cell["cell_status"] = "unread_cell_candidate"
                    unresolved += 1
                else:
                    cell["cell_status"] = "text_detected"
                continue
            crop, lx0, ly0 = inset
            gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY) if crop.ndim == 3 else crop
            # Table borders are not evidence of cell text. Remove long rules before
            # deciding whether an empty OCR result deserves a second read.
            binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
            horizontal = cv2.morphologyEx(
                binary, cv2.MORPH_OPEN,
                cv2.getStructuringElement(cv2.MORPH_RECT, (max(12, crop.shape[1] // 3), 1)),
            )
            vertical = cv2.morphologyEx(
                binary, cv2.MORPH_OPEN,
                cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(12, crop.shape[0] // 3))),
            )
            text_ink = cv2.subtract(binary, cv2.bitwise_or(horizontal, vertical))
            ink_ratio = float(np.count_nonzero(text_ink) / text_ink.size) if text_ink.size else 0.0
            crop_text = ""
            crop_score = 0.0
            crop_boxes: list[dict] = []
            if ink_ratio > 0.006 and crop.size:
                factor = 3.0 if min(crop.shape[:2]) < 24 else 2.0
                enlarged = cv2.resize(gray, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
                enlarged = cv2.cvtColor(enlarged, cv2.COLOR_GRAY2RGB)
                crop_result, _ = text_engine(enlarged)
                crop_items = sorted(crop_result or [], key=lambda item: (
                    min(point[1] for point in item[0]), min(point[0] for point in item[0]),
                ))
                crop_text = "".join(str(item[1]).strip() for item in crop_items if str(item[1]).strip())
                crop_scores = [float(item[2]) for item in crop_items if str(item[1]).strip()]
                crop_score = min(crop_scores, default=0.0)
                for result_box, text, score in crop_items:
                    if not str(text).strip():
                        continue
                    mapped = np.asarray(result_box, dtype=float) / factor
                    mapped[:, 0] += lx0 + int(page_x0)
                    mapped[:, 1] += ly0 + int(page_y0)
                    recovered = _box_record(mapped.tolist(), str(text), float(score), next_source_index)
                    next_source_index += 1
                    crop_boxes.append(recovered)
                    recovered_boxes.append(recovered)
            use_crop = bool(crop_text) and crop_score >= 0.45 and (
                not cell["text"] or crop_score >= weakest_score + 0.08
            )
            if use_crop:
                for original in cell_boxes:
                    original["omit_from_pdf"] = True
                cell["text"] = crop_text
                cell["source_indices"] = [item["source_index"] for item in crop_boxes]
                cell_boxes = crop_boxes
                cell["cell_status"] = "crop_reread_text"
            elif not cell["text"] and ink_ratio <= 0.006:
                cell["cell_status"] = "blank_candidate"
            elif cell["text"] and weakest_score >= 0.55:
                cell["cell_status"] = "text_detected"
            else:
                cell["cell_status"] = "unread_cell_candidate"
                unresolved += 1
        elif weakest_score < 0.55:
            cell["cell_status"] = "low_confidence_text"
            unresolved += 1
    return parser.cells, float(payload.get("structure_score") or 0.0), unresolved, recovered_boxes, reread_changes


def _page_blocks(
    image: np.ndarray,
    boxes: list[dict],
    layout_engine: LayoutDetection,
    table_engines: dict[str, TableStructureRecognition],
    text_engine: RapidOCR,
    text_rec_engine: object,
    source_page: int,
) -> tuple[list[Block], int, int, int, int]:
    height, width = image.shape[:2]
    prediction = layout_engine.predict(image)
    payload = _res_payload(prediction[0]) if prediction else {}
    layout_regions = payload.get("boxes", [])
    blocks: list[Block] = []
    table_box_indexes: set[int] = set()
    unresolved_cells = 0
    reread_boxes = 0
    reread_changes = 0
    for region in layout_regions:
        label = str(region.get("label", "text")).lower()
        bbox = _clip_bbox(region.get("coordinate", [0, 0, width, height]), width, height)
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        contained = [index for index, box in enumerate(boxes) if _inside(box, bbox)]
        if label == "table":
            x0, y0, x1, y1 = (int(v) for v in bbox)
            crop = image[y0:y1, x0:x1]
            next_index = max((box["source_index"] for box in boxes), default=-1) + 1
            cells, structure_score, cell_failures, recovered, changed = _table_cells(
                crop, bbox, boxes, table_engines, text_engine, text_rec_engine, next_index,
            )
            unresolved_cells += cell_failures
            reread_boxes += len(recovered)
            reread_changes += changed
            recovered_start = len(boxes)
            boxes.extend(recovered)
            contained.extend(range(recovered_start, len(boxes)))
            table_box_list = [boxes[item] for item in contained if item < len(boxes)]
            blocks.append(Block(
                "table", bbox, min(float(region.get("score", 0.0)), structure_score),
                region.get("order"),
                "paddleocr-layout+" + ("slanext-wired" if _is_ruled_table(crop) else "slanet-plus"),
                table_box_list, cells, label,
                "candidate" if structure_score >= 0.80 and cell_failures == 0 else "needs_review",
                source_page,
            ))
            table_box_indexes.update(contained)
        elif label in {"image", "figure", "chart"}:
            blocks.append(Block("figure", bbox, float(region.get("score", 0.0)), region.get("order"),
                                "paddleocr-layout", [boxes[index] for index in contained], label=label,
                                source_page=source_page))
        else:
            kind = "text" if label not in {"header", "footer", "page_number", "number"} else "context"
            blocks.append(Block(kind, bbox, float(region.get("score", 0.0)), region.get("order"),
                                "paddleocr-layout+rapidocr", [boxes[index] for index in contained], label=label,
                                source_page=source_page))

    assigned = {id(box) for block in blocks for box in block.boxes}
    for box in boxes:
        if id(box) in assigned:
            continue
        rect = box["bbox_px"]
        bbox = (rect["x0"], rect["y0"], rect["x1"], rect["y1"])
        blocks.append(Block("text", bbox, box["score"], None, "rapidocr-unclassified-fallback", [box],
                            status="fallback_region", source_page=source_page))
    blocks.sort(key=lambda block: (
        block.reading_order is None,
        block.reading_order if block.reading_order is not None else block.bbox_px[1],
        block.bbox_px[0],
    ))
    for order, block in enumerate(blocks):
        block.reading_order = order
    return blocks, len(layout_regions), unresolved_cells, reread_boxes, reread_changes


def _ordered_boxes(blocks: list[Block]) -> list[dict]:
    ordered: list[dict] = []
    seen: set[int] = set()
    for block in blocks:
        if block.kind == "table" and block.cells:
            cell_order = sorted(block.cells, key=lambda cell: (cell["row"], cell["column"]))
            for cell in cell_order:
                for source_index in cell.get("source_indices", []):
                    if source_index not in seen:
                        found = next((box for box in block.boxes if box["source_index"] == source_index), None)
                        if found is not None:
                            ordered.append(found)
                            seen.add(source_index)
        for box in sorted(block.boxes, key=lambda item: (item["bbox_px"]["y0"], item["bbox_px"]["x0"])):
            if box.get("omit_from_pdf"):
                continue
            if box["source_index"] not in seen:
                ordered.append(box)
                seen.add(box["source_index"])
    # Layout-model order can disagree with the visible page order (especially
    # on contents pages). Rebuild a stable geometric sequence from the boxes.
    return [box for line in _group_lines(ordered) for box in line]


def _insert_positioned_text(page: fitz.Page, boxes: list[dict], font_file: Path | None) -> int:
    font_paths = _font_paths(font_file)
    fonts = [fitz.Font(fontfile=str(path)) for path in font_paths]
    if not fonts:
        fonts = [fitz.Font("china-s")]
        font_paths = []
    font_names = [f"ocr-font-{index}" for index in range(len(fonts))]

    def line_runs(line: str) -> list[tuple[str, int]] | None:
        runs: list[tuple[str, int]] = []
        for character in line:
            font_index = next(
                (index for index, font in enumerate(fonts) if font.has_glyph(ord(character))),
                None,
            )
            if font_index is None:
                return None
            if runs and runs[-1][1] == font_index:
                runs[-1] = (runs[-1][0] + character, font_index)
            else:
                runs.append((character, font_index))
        return runs

    failures = 0
    for box in boxes:
        bbox = box.get("bbox_pt")
        if not bbox:
            continue
        rect = fitz.Rect(bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"])
        text = box["text"].strip()
        if not text or rect.is_empty:
            continue
        lines = text.splitlines() or [text]
        line_data = [line_runs(line) for line in lines]
        if any(runs is None for runs in line_data):
            failures += 1
            continue
        line_widths = [
            sum(fonts[index].text_length(run, fontsize=1.0) for run, index in runs or [])
            for runs in line_data
        ]
        font_size = min(18.0, rect.height / max(1.2, len(lines) * 1.15))
        if max(line_widths, default=0.0) > 0:
            font_size = min(font_size, rect.width / max(line_widths))
        if font_size < 1.0:
            failures += 1
            continue
        line_height = font_size * 1.05
        for line_index, runs in enumerate(line_data):
            cursor_x = rect.x0
            baseline = min(rect.y1, rect.y0 + font_size * 0.9 + line_index * line_height)
            for run, font_index in runs or []:
                kwargs = {"fontname": font_names[font_index]}
                if font_paths:
                    kwargs["fontfile"] = str(font_paths[font_index])
                try:
                    page.insert_text((cursor_x, baseline), run, fontsize=font_size,
                                     render_mode=3, overlay=True, **kwargs)
                except Exception:
                    failures += 1
                    break
                cursor_x += fonts[font_index].text_length(run, fontsize=font_size)
    return failures


def generate(
    source: Path,
    output: Path,
    dpi: int,
    first_page: int,
    last_page: int | None,
    font_file: Path | None,
) -> None:
    # Keep module import side-effect free for helpers and tests. PaddleX creates
    # a cache at import time, so load its pipelines only when generation starts.
    from paddleocr import LayoutDetection, TableStructureRecognition, TextRecognition

    output = validate_output_path(output)
    source_doc = fitz.open(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    output_doc = fitz.open()
    text_engine = RapidOCR(det_limit_side_len=1024, max_side_len=3200,
                           det_box_thresh=0.35, det_thresh=0.15, text_score=0.25)
    layout_engine = LayoutDetection(model_name="PP-DocLayoutV2", engine="onnxruntime", device="cpu")
    table_engines = {
        "general": TableStructureRecognition(model_name="SLANet_plus", engine="onnxruntime", device="cpu"),
        "wired": TableStructureRecognition(model_name="SLANeXt_wired", engine="onnxruntime", device="cpu"),
    }
    text_rec_engine = TextRecognition(model_name="PP-OCRv5_server_rec", engine="onnxruntime", device="cpu")
    start_index = first_page - 1
    end_index = len(source_doc) if last_page is None else min(last_page, len(source_doc))
    if start_index < 0 or start_index >= end_index:
        source_doc.close()
        output_doc.close()
        raise ValueError("invalid page range")

    totals = {"pages": 0, "zero_text_pages": 0, "layout_regions": 0,
              "tables": 0, "unresolved_cells": 0, "cell_crop_reread_boxes": 0,
              "text_insert_failures": 0, "table_text_reread_changes": 0,
              "risk_text_reread_changes": 0,
              "tables_needing_review": 0}
    scale = dpi / 72.0
    temp_path: Path | None = None
    try:
        for index in range(start_index, end_index):
            source_page = source_doc[index]
            pixmap = source_page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
            result, _ = text_engine(image)
            boxes = [_box_record(box, text, score, order) for order, (box, text, score) in enumerate(result or []) if text]
            page_blocks, region_count, unresolved, reread_count, text_changes = _page_blocks(
                image, boxes, layout_engine, table_engines, text_engine, text_rec_engine, index + 1,
            )
            table_box_ids = {
                id(box) for block in page_blocks if block.kind == "table" for box in block.boxes
            }
            risk_boxes = [box for box in boxes if id(box) not in table_box_ids]
            risk_reread_changes = _reread_risk_boxes(image, risk_boxes, text_rec_engine)
            ordered = _ordered_boxes(page_blocks)
            page_width, page_height = source_page.rect.width, source_page.rect.height
            sx, sy = page_width / pixmap.width, page_height / pixmap.height
            for box in ordered:
                bbox = box["bbox_px"]
                box["bbox_pt"] = {
                    "x0": round(bbox["x0"] * sx, 3), "y0": round(bbox["y0"] * sy, 3),
                    "x1": round(bbox["x1"] * sx, 3), "y1": round(bbox["y1"] * sy, 3),
                }
            page = output_doc.new_page(width=page_width, height=page_height)
            page.insert_image(page.rect, stream=pixmap.tobytes("png"))
            text_failures = _insert_positioned_text(page, ordered, font_file)
            visible_text = sum(len(box["text"].strip()) for box in ordered)
            totals["pages"] += 1
            totals["zero_text_pages"] += int(visible_text == 0)
            totals["layout_regions"] += region_count
            totals["tables"] += sum(block.kind == "table" for block in page_blocks)
            totals["unresolved_cells"] += unresolved
            totals["cell_crop_reread_boxes"] += reread_count
            totals["table_text_reread_changes"] += text_changes
            totals["risk_text_reread_changes"] += risk_reread_changes
            tables_needing_review = sum(
                block.kind == "table" and block.status == "needs_review" for block in page_blocks
            )
            totals["tables_needing_review"] += tables_needing_review
            totals["text_insert_failures"] += text_failures
            print(f"page {index + 1}/{len(source_doc)} run={totals['pages']}/{end_index - start_index}: "
                  f"text_boxes={len(ordered)} blocks={len(page_blocks)} "
                  f"tables={sum(block.kind == 'table' for block in page_blocks)} unresolved_cells={unresolved} "
                  f"needs_review_tables={tables_needing_review} insert_failures={text_failures}", flush=True)

        output_doc.set_metadata({**source_doc.metadata, "title": source_doc.metadata.get("title") or source.stem})
        if totals["text_insert_failures"]:
            raise RuntimeError(f"OCR text layer insertion failed: {totals['text_insert_failures']} boxes")
        with tempfile.NamedTemporaryFile(prefix=f"{output.stem}.", suffix=".tmp.pdf",
                                         dir=output.parent, delete=False) as handle:
            temp_path = Path(handle.name)
        output_doc.save(temp_path, garbage=4, deflate=True)
        output_doc.close()
        check_doc = fitz.open(temp_path)
        if len(check_doc) != totals["pages"]:
            raise RuntimeError(f"output page count mismatch: expected={totals['pages']} actual={len(check_doc)}")
        for page_number, page in enumerate(check_doc, start=1):
            extracted = page.get_text()
            if "\x00" in extracted or "\ufffd" in extracted or not page.get_images():
                raise RuntimeError(f"unreadable or incomplete output page {page_number}")
        check_doc.close()
        os.replace(temp_path, output)
        temp_path = None
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()
        source_doc.close()
        if not output_doc.is_closed:
            output_doc.close()
    print(f"OCR PDF written: {output}")
    print("run totals: " + ", ".join(f"{key}={value}" for key, value in totals.items()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--first-page", type=int, default=1)
    parser.add_argument("--last-page", type=int)
    parser.add_argument("--font-file", type=Path, default=default_font_file())
    args = parser.parse_args()
    generate(args.source, args.output, args.dpi, args.first_page, args.last_page, args.font_file)


if __name__ == "__main__":
    main()
