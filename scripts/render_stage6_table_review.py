"""Render reviewed table regions on Original materials pages for Stage 6 QA."""

from __future__ import annotations

import io
import json
from collections import defaultdict
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

from turbine_kg.settings import Settings


ROOT = Path(__file__).resolve().parents[1]
DECISIONS = ROOT / "data" / "stage6" / "stage6_table_review_decisions.jsonl"
OUTPUT = ROOT / "tmp" / "pdfs" / "stage6"
ORIGINALS = {
    "DL5190.3": "标准法规/DL5190.3—2019电力建设施工技术规范第3部分：汽轮发电机组.pdf",
    "D300N": "汽轮机说明书/汽轮机本体安装及维护说明书.pdf",
    "DLT863": "标准法规/DLT 863-2016汽轮机启动调试导则.pdf",
    "HAF103": "标准法规/HAF103核动力厂调试和运行安全规定.pdf",
    "auxiliary_installation_book": "2.书籍/260824 扫描文件/汽轮机辅机安装（第二版）.pdf",
}


def _render(path: Path, page_number: int, regions: list[dict]) -> Image.Image:
    with pymupdf.open(path) as document:
        page = document[page_number - 1]
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.3, 1.3), alpha=False)
        image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
    draw = ImageDraw.Draw(image)
    for index, row in enumerate(regions, start=1):
        bbox = row["bbox"]
        rect = tuple(float(bbox[name]) * 1.3 for name in ("x0", "y0", "x1", "y1"))
        draw.rectangle(rect, outline=(255, 0, 0), width=3)
        draw.text((rect[0] + 3, rect[1] + 3), f"{index}: {row['table_label']}", fill=(255, 0, 0))
    return image


def main() -> None:
    settings = Settings.from_environment()
    rows = [json.loads(line) for line in DECISIONS.read_text(encoding="utf-8").splitlines() if line.strip()]
    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row["document_key"], int(row["physical_page"]))].append(row)
    rendered = []
    for (document_key, page_number), regions in sorted(grouped.items()):
        image = _render(settings.source_root / ORIGINALS[document_key], page_number, regions)
        canvas = Image.new("RGB", (image.width, image.height + 32), "white")
        canvas.paste(image, (0, 32))
        ImageDraw.Draw(canvas).text((8, 8), f"{document_key} physical {page_number} - Original materials", fill="black")
        rendered.append(canvas)
    for index in range(0, len(rendered), 3):
        group = rendered[index:index + 3]
        width = max(image.width for image in group)
        sheet = Image.new("RGB", (width, sum(image.height for image in group)), "white")
        y = 0
        for image in group:
            sheet.paste(image, (0, y))
            y += image.height
        output = OUTPUT / f"table-review-{index // 3 + 1:02d}.png"
        sheet.save(output)
        print(output)


if __name__ == "__main__":
    main()
