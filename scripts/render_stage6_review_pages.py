"""Render original Stage 6 Golden Sample pages with review-only overlays."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

from turbine_kg.settings import Settings


ROOT = Path(__file__).resolve().parents[1]
STAGE6 = ROOT / "data" / "stage6"
OUTPUT = ROOT / "tmp" / "pdfs" / "stage6"
ORIGINALS = {
    "DL5190.3": "标准法规/DL5190.3—2019电力建设施工技术规范第3部分：汽轮发电机组.pdf",
    "D300N": "汽轮机说明书/汽轮机本体安装及维护说明书.pdf",
    "DLT863": "标准法规/DLT 863-2016汽轮机启动调试导则.pdf",
    "HAF103": "标准法规/HAF103核动力厂调试和运行安全规定.pdf",
    "auxiliary_installation_book": "2.书籍/260824 扫描文件/汽轮机辅机安装（第二版）.pdf",
}
CONTINUATION_CONTEXT = {
    ("D300N", 50): (49, 51),
    ("DLT863", 27): (28,),
    ("DLT863", 28): (27,),
}


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _draw_evidence(page: pymupdf.Page, rows: list[dict]) -> None:
    for ordinal, row in enumerate(rows, start=1):
        evidence = row["evidence"]
        for location in evidence["locations"]:
            bbox = location.get("bbox")
            if not bbox:
                continue
            rect = pymupdf.Rect(bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"])
            page.draw_rect(rect, color=(1, 0, 0), width=1.2, overlay=True)
            page.insert_text(
                (rect.x0, max(8, rect.y0 - 2)),
                str(ordinal),
                fontsize=6,
                color=(1, 0, 0),
                overlay=True,
            )


def _render(path: Path, page_number: int, output: Path, rows: list[dict]) -> None:
    with pymupdf.open(path) as document:
        page = document[page_number - 1]
        _draw_evidence(page, rows)
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0), alpha=False)
        pixmap.save(output)


def _build_contact_sheets(manifest: list[dict]) -> list[str]:
    outputs = []
    for sheet_index, offset in enumerate(range(0, len(manifest), 4), start=1):
        batch = manifest[offset:offset + 4]
        loaded = [(row, Image.open(ROOT / row["render"]).convert("RGB")) for row in batch]
        cell_width = max(image.width for _, image in loaded)
        cell_height = max(image.height for _, image in loaded) + 34
        canvas = Image.new("RGB", (cell_width * 2, cell_height * 2), "white")
        draw = ImageDraw.Draw(canvas)
        for index, (row, source) in enumerate(loaded):
            x = (index % 2) * cell_width
            y = (index // 2) * cell_height
            canvas.paste(source, (x, y + 34))
            label = f"{row['document_key']} p{row['physical_page']} evidence={row['evidence_count']}"
            if row["context_only"]:
                label += " context"
            draw.text((x + 8, y + 10), label, fill="black")
        output = OUTPUT / f"contact-{sheet_index:02d}.png"
        canvas.save(output)
        for _, image in loaded:
            image.close()
        outputs.append(str(output.relative_to(ROOT)).replace("\\", "/"))
    return outputs


def main() -> None:
    settings = Settings.from_environment()
    golden = json.loads((STAGE6 / "stage6_evidence_golden_sample.json").read_text(encoding="utf-8"))
    annotations = _load_jsonl(STAGE6 / "stage6_evidence_annotations.jsonl")
    evidence_by_page: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in annotations:
        location = row["evidence"]["locations"][0]
        evidence_by_page[(row["document_key"], int(location["physical_page"]))].append(row)

    pages = {
        (row["document_key"], int(row["physical_page"]))
        for row in golden["records"]
        if row["evidence_eligibility"] in {"structured_candidate", "region_scoped", "quarantined"}
    }
    context_pages: set[tuple[str, int]] = set()
    for key in pages:
        for page_number in CONTINUATION_CONTEXT.get(key, ()):
            context_pages.add((key[0], page_number))

    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    for document_key, page_number in sorted(pages | context_pages):
        is_context_only = (document_key, page_number) not in pages
        suffix = "context" if is_context_only else "review"
        output = OUTPUT / f"{document_key}-original-p{page_number:03d}-{suffix}.png"
        rows = [] if is_context_only else evidence_by_page.get((document_key, page_number), [])
        _render(settings.source_root / ORIGINALS[document_key], page_number, output, rows)
        manifest.append({
            "document_key": document_key,
            "physical_page": page_number,
            "context_only": is_context_only,
            "evidence_count": len(rows),
            "original_relative_path": ORIGINALS[document_key],
            "render": str(output.relative_to(ROOT)).replace("\\", "/"),
        })

    (OUTPUT / "review_manifest.json").write_text(
        json.dumps({"pages": manifest, "contact_sheets": _build_contact_sheets(manifest)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"page_count": len(manifest), "output": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
