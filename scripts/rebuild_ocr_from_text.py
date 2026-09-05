"""Rebuild an OCR derivative using its existing hidden text plus reviewed fixes."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import fitz

from generate_ocr_pdf import default_font_file, patch_unicode_map


def rebuild(source: Path, text_source: Path, output: Path, corrections_file: Path, dpi: int) -> None:
    source_doc = fitz.open(source)
    text_doc = fitz.open(text_source)
    if len(source_doc) != len(text_doc):
        raise ValueError(f"page count mismatch: source={len(source_doc)} text={len(text_doc)}")
    corrections = json.loads(corrections_file.read_text(encoding="utf-8"))
    rebuilt = fitz.open()
    page_texts: list[str] = []
    font_file = default_font_file()
    for index, source_page in enumerate(source_doc):
        text = text_doc[index].get_text("text")
        correction = corrections.get(str(index + 1), {})
        if "text_override" in correction:
            text = str(correction["text_override"])
        for replacement in correction.get("replacements", []):
            old = str(replacement["old"])
            new = str(replacement["new"])
            if old not in text:
                escaped_old = old.encode("unicode_escape").decode("ascii")
                print(f"warning: correction source not found on page {index + 1}: {escaped_old}")
            text = text.replace(old, new)
        page_texts.append(text)
        scale = dpi / 72.0
        pixmap = source_page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        page = rebuilt.new_page(width=source_page.rect.width, height=source_page.rect.height)
        page.insert_image(page.rect, stream=pixmap.tobytes("png"))
        if text.strip():
            kwargs = {
                "rect": page.rect,
                "buffer": text,
                "fontsize": 1,
                "render_mode": 3,
                "overlay": True,
                "fontname": "ocr-cjk" if font_file else "china-s",
            }
            if font_file:
                kwargs["fontfile"] = str(font_file)
            page.insert_textbox(**kwargs)
    rebuilt.set_metadata(dict(source_doc.metadata))
    patch_unicode_map(rebuilt, page_texts)
    temp = output.with_name(output.stem + ".rebuild.tmp.pdf")
    if temp.exists():
        temp.unlink()
    rebuilt.save(temp, garbage=4, deflate=True)
    rebuilt.close()
    source_doc.close()
    text_doc.close()
    os.replace(temp, output)
    print(f"ocr pdf rebuilt: {output}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("text_source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("corrections_json", type=Path)
    parser.add_argument("--dpi", type=int, default=170)
    args = parser.parse_args()
    rebuild(args.source, args.text_source, args.output, args.corrections_json, args.dpi)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
