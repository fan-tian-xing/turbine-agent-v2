"""Generate a local OCR-PDF derivative while preserving the source page images."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

import fitz
import numpy as np
from rapidocr_onnxruntime import RapidOCR


def grouped_text(result: list) -> str:
    if not result:
        return ""
    items = []
    for box, text, score in result:
        if not text or float(score) < 0.25:
            continue
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        height = max(1.0, max(ys) - min(ys))
        items.append((min(ys), min(xs), height, text))
    items.sort(key=lambda item: (item[0], item[1]))
    lines: list[list[tuple[float, float, float, str]]] = []
    for item in items:
        if not lines or abs(item[0] - lines[-1][0][0]) > max(8.0, item[2] * 0.7):
            lines.append([item])
        else:
            lines[-1].append(item)
    return "\n".join("".join(item[3] for item in line) for line in lines)


def default_font_file() -> Path | None:
    candidates = [
        Path(os.environ.get("OCR_FONT_FILE", "")),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]
    return next((path for path in candidates if str(path) and path.is_file()), None)


def patch_unicode_map(document: fitz.Document, page_texts: list[str]) -> None:
    """Replace PyMuPDF's approximate CMap with the exact OCR text mapping.

    PyMuPDF embeds a font for CJK text, but its automatically generated
    ToUnicode ranges can be lossy for Chinese glyph IDs.  The content stream
    and the OCR text are both available here, so build an exact glyph-code to
    Unicode map for reliable downstream extraction.
    """
    mappings: dict[int, str] = {}
    conflicts: list[tuple[int, str, str]] = []
    for page, expected_text in zip(document, page_texts):
        fonts = page.get_fonts(full=True)
        type0_fonts = [font for font in fonts if font[2] == "Type0"]
        if not type0_fonts:
            continue
        font_xref = type0_fonts[0][0]
        tounicode = document.xref_get_key(font_xref, "ToUnicode")[1]
        if not tounicode or not tounicode.endswith("R"):
            continue
        tounicode_xref = int(tounicode.split()[0])
        content = b"".join(document.xref_stream(xref) or b"" for xref in page.get_contents())
        encoded = re.findall(rb"<([0-9A-Fa-f]+)>", content)
        code_units = [
            int(chunk[offset : offset + 4], 16)
            for chunk in encoded
            for offset in range(0, len(chunk), 4)
        ]
        characters = [char for char in expected_text if char != "\n"]
        if len(code_units) != len(characters):
            print(
                "warning: Unicode map skipped on page "
                f"{page.number + 1}: encoded={len(code_units)} text={len(characters)}"
            )
            continue
        for code, character in zip(code_units, characters):
            previous = mappings.get(code)
            if previous is not None and previous != character:
                conflicts.append((code, previous, character))
            else:
                mappings[code] = character

        if not mappings:
            continue
        newline = "\n"
        cmap = (
            "/CIDInit /ProcSet findresource begin\n"
            "12 dict begin\n"
            "begincmap\n"
            "/CIDSystemInfo <</Registry (Adobe)/Ordering (UCS)/Supplement 0>> def\n"
            "/CMapName /Adobe-Identity-UCS def\n"
            "/CMapType 2 def\n"
            "1 begincodespacerange\n"
            "<0000> <FFFF>\n"
            "endcodespacerange\n"
            f"{len(mappings)} beginbfchar\n"
            + "".join(
                f"<{code:04X}> <{character.encode('utf-16-be').hex().upper()}>\n"
                for code, character in sorted(mappings.items())
            )
            + "endbfchar\n"
            "endcmap\n"
            "CMapName currentdict /CMap defineresource pop\n"
            "end\n"
            "end\n"
        )
        document.update_stream(tounicode_xref, cmap.encode("ascii"))

    if conflicts:
        print(f"warning: Unicode glyph conflicts encountered: {len(conflicts)}")


def generate(
    source: Path,
    output: Path,
    dpi: int,
    first_page: int,
    last_page: int | None,
    font_file: Path | None,
    corrections_file: Path | None = None,
) -> None:
    source_doc = fitz.open(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    output_doc = fitz.open()
    engine = RapidOCR()
    total_chars = 0
    page_texts: list[str] = []
    corrections: dict[str, dict] = {}
    if corrections_file is not None:
        corrections = json.loads(corrections_file.read_text(encoding="utf-8"))
    start_index = first_page - 1
    end_index = len(source_doc) if last_page is None else min(last_page, len(source_doc))
    if start_index < 0 or start_index >= end_index:
        raise ValueError("invalid page range")
    for index in range(start_index, end_index):
        source_page = source_doc[index]
        scale = dpi / 72.0
        pixmap = source_page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        image_bytes = pixmap.tobytes("png")
        image_array = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width, pixmap.n
        )
        result, _ = engine(image_array)
        text = grouped_text(result or [])
        correction = corrections.get(str(index + 1), {})
        if "text_override" in correction:
            text = str(correction["text_override"])
        for replacement in correction.get("regex_replacements", []):
            pattern = str(replacement["pattern"])
            new = str(replacement["new"])
            text, count = re.subn(pattern, new, text)
            if count == 0:
                print(
                    "warning: regex correction source not found on page "
                    f"{index + 1}: {pattern.encode('unicode_escape').decode('ascii')}"
                )
        for replacement in correction.get("replacements", []):
            old = str(replacement["old"])
            new = str(replacement["new"])
            if old not in text:
                print(
                    "warning: correction source not found on page "
                    f"{index + 1}: {old.encode('unicode_escape').decode('ascii')}"
                )
            text = text.replace(old, new)
        page_texts.append(text)
        total_chars += len(text)
        page = output_doc.new_page(width=source_page.rect.width, height=source_page.rect.height)
        page.insert_image(page.rect, stream=image_bytes)
        if text:
            insert_kwargs = {
                "rect": page.rect,
                "buffer": text,
                "fontsize": 1,
                "render_mode": 3,
                "overlay": True,
            }
            if font_file is not None:
                insert_kwargs["fontfile"] = str(font_file)
                insert_kwargs["fontname"] = "ocr-cjk"
            else:
                insert_kwargs["fontname"] = "china-s"
            page.insert_textbox(**insert_kwargs)
        print(f"page {index + 1}/{len(source_doc)}: {len(text)} chars")
    metadata = dict(source_doc.metadata)
    metadata["title"] = metadata.get("title") or source.stem
    output_doc.set_metadata(metadata)
    patch_unicode_map(output_doc, page_texts)
    output_doc.save(output, garbage=4, deflate=True)
    output_doc.close()
    source_doc.close()
    print(f"ocr pdf written: {output} ({total_chars} chars)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--dpi", type=int, default=170)
    parser.add_argument("--first-page", type=int, default=1)
    parser.add_argument("--last-page", type=int)
    parser.add_argument("--font-file", type=Path, default=default_font_file())
    parser.add_argument("--corrections-json", type=Path)
    args = parser.parse_args()
    generate(
        args.source,
        args.output,
        args.dpi,
        args.first_page,
        args.last_page,
        args.font_file,
        args.corrections_json,
    )


if __name__ == "__main__":
    main()
