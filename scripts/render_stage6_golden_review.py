"""Render Stage 6 Golden Sample original/processing pairs with Evidence bboxes."""

from __future__ import annotations

import io
import json
from collections import defaultdict
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

from turbine_kg.settings import Settings


ROOT = Path(__file__).resolve().parents[1]
ANNOTATION_FILES = (
    ROOT / "data" / "stage6" / "stage6_evidence_annotations.jsonl",
    ROOT / "data" / "stage6" / "stage6_table_evidence_annotations.jsonl",
)
OUTPUT = ROOT / "tmp" / "pdfs" / "stage6"


def _page_image(path: Path, page_number: int, bboxes: list[dict] | None = None) -> Image.Image:
    with pymupdf.open(path) as document:
        page = document[page_number - 1]
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.0, 1.0), alpha=False)
        image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
        if bboxes:
            draw = ImageDraw.Draw(image)
            for bbox in bboxes:
                draw.rectangle(
                    (bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"]),
                    outline=(255, 0, 0),
                    width=1,
                )
        return image


def _fit(image: Image.Image, width: int = 520) -> Image.Image:
    height = round(image.height * width / image.width)
    return image.resize((width, height), Image.Resampling.LANCZOS)


def main() -> None:
    settings = Settings.from_environment()
    rows = [
        json.loads(line)
        for path in ANNOTATION_FILES
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    pages: dict[tuple[str, int], list[dict]] = defaultdict(list)
    inputs: dict[tuple[str, int], dict] = {}
    for row in rows:
        key = (row["document_key"], row["input"]["physical_page"])
        inputs[key] = row["input"]
        pages[key].extend(location["bbox"] for location in row["evidence"]["locations"] if location["bbox"])

    OUTPUT.mkdir(parents=True, exist_ok=True)
    chunks: dict[str, list[tuple[tuple[str, int], Image.Image]]] = defaultdict(list)
    for key in sorted(pages):
        document_key, page_number = key
        item = inputs[key]
        original_path = settings.source_root / item["original_relative_path"]
        processing_path = original_path
        if item["processing_relative_path"]:
            processing_path = settings.ocr_derived_root / item["processing_relative_path"]
        original = _fit(_page_image(original_path, page_number, pages[key]))
        processing = _fit(_page_image(processing_path, page_number))
        row_height = max(original.height, processing.height) + 44
        pair = Image.new("RGB", (1060, row_height), "white")
        pair.paste(original, (0, 34))
        pair.paste(processing, (540, 34))
        draw = ImageDraw.Draw(pair)
        draw.text((8, 8), f"{document_key} physical {page_number} | Original + Evidence bboxes", fill="black")
        draw.text((548, 8), "Processing view (OCR only when needed)", fill="black")
        chunks[document_key].append((key, pair))

    outputs = []
    for document_key, document_pages in chunks.items():
        for chunk_index in range(0, len(document_pages), 4):
            group = document_pages[chunk_index:chunk_index + 4]
            sheet = Image.new("RGB", (1060, sum(image.height for _, image in group)), "white")
            y = 0
            for _, pair in group:
                sheet.paste(pair, (0, y))
                y += pair.height
            output = OUTPUT / f"golden-review-{document_key}-{chunk_index // 4 + 1:02d}.png"
            sheet.save(output)
            outputs.append(str(output))
    print(json.dumps(outputs, ensure_ascii=False))


if __name__ == "__main__":
    main()
