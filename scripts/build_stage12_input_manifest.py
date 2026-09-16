"""Freeze a label-free, development-only representative Stage 12 input view."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/stage12/stage12_input_manifest.json"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build() -> dict:
    audit = json.loads((ROOT / "data/stage11/stage11_exit_audit.json").read_text(encoding="utf-8"))
    if audit.get("status") != "complete" or not audit.get("checks", {}).get("stage12_entry_allowed"):
        raise ValueError("Stage 11 is not open for Stage 12")
    gold = _rows(ROOT / "data/stage11/stage11_statement_development_samples.jsonl")
    evidence = {row["evidence"]["evidence_id"]: row["evidence"] for row in _rows(ROOT / "data/stage6/stage6_evidence_bundle.jsonl")}
    # Selection uses only the development Gold as a development-time coverage
    # signal.  The resulting manifest contains no Gold labels or expected
    # Statements and is the only input view opened by the extractor.
    groups: dict[tuple[str, int], list[dict]] = {}
    for row in gold:
        groups.setdefault((row["document_key"], int(row["physical_page"])), []).append(row)
    feature_tokens = {
        "numeric_unit": ("mm", "kPa", "%", "℃"),
        "range": ("~", "～", "范围"),
        "negation": ("不得", "不应", "不小于", "不大于", "无", "未", "不入"),
        "condition": ("当", "若", "如果", "时", "状态下"),
        "multi_object_or_step": ("然后", "再", "并", "、", "及"),
        "enumeration": ("1、", "2、", "3、", "4、", "5、"),
    }
    scored = []
    for page, rows in groups.items():
        text = "\n".join(row.get("statement_text", "") for row in rows)
        evidence_text = "\n".join(
            evidence[binding["evidence_id"]].get("effective_text", evidence[binding["evidence_id"]].get("source_text", ""))
            for row in rows for binding in row.get("evidence_bindings", []) if binding["evidence_id"] in evidence
        )
        combined = text + "\n" + evidence_text
        coverage = [name for name, tokens in feature_tokens.items() if any(token in combined for token in tokens)]
        if re.search(r"(?:^|\n|\s)[1-9][、.)．]", combined):
            coverage.append("enumeration")
        scored.append((len(set(coverage)), len(rows), page, coverage, rows))
    selected: list[tuple[str, int]] = []
    # One highest-coverage page per admitted document keeps the first slice
    # small while ensuring every source profile is represented.
    for document_key in sorted({page[0] for page in groups}):
        options = [item for item in scored if item[2][0] == document_key]
        selected.append(max(options, key=lambda item: (item[0], item[1], -item[2][1]))[2])
        multi_statement_pages = [item for item in options if item[1] >= 4 and item[2] not in selected]
        if multi_statement_pages:
            selected.append(max(multi_statement_pages, key=lambda item: (item[1], item[0], -item[2][1]))[2])
    # Add pages that contribute a feature not present in the initial slice.
    covered = set()
    for page in selected:
        covered.update(next(item[3] for item in scored if item[2] == page))
    for item in sorted(scored, key=lambda value: (-value[0], value[2])):
        if len(selected) >= 8:
            break
        if set(item[3]) - covered:
            selected.append(item[2])
            covered.update(item[3])
    # Keep all remaining development pages when the curated development slice
    # is smaller than the eight-page cap. This preserves the Stage 11 designed
    # coverage (for example separate condition pages) without opening any
    # holdout content.
    for page in sorted(groups):
        if len(selected) >= 8:
            break
        if page not in selected:
            selected.append(page)
    selected = sorted(set(selected))
    pages = []
    for document_key, physical_page in selected:
        rows = groups[(document_key, physical_page)]
        ids = sorted({binding["evidence_id"] for row in rows for binding in row.get("evidence_bindings", [])})
        if not ids or any(evidence_id not in evidence for evidence_id in ids):
            raise ValueError(f"representative page has no canonical Evidence: {document_key} p{physical_page}")
        page_evidence = [evidence[evidence_id] for evidence_id in ids]
        pages.append({
            "document_key": document_key,
            "physical_page": physical_page,
            "document_logical_id": page_evidence[0]["document_logical_id"],
            "revision_id": page_evidence[0]["revision_id"],
            "evidence_ids": ids,
            "source_text_sha256": sorted({item["source_text_sha256"] for item in page_evidence}),
            "coverage": next(item[3] for item in scored if item[2] == (document_key, physical_page)),
            "section_path": "chapter_unknown",
        })
    return {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "stage12_development_input_manifest",
        "status": "frozen",
        "formal_release": False,
        "producer": "scripts/build_stage12_input_manifest.py",
        "source_split": "development_regression_golden",
        "label_free_extractor_view": True,
        "holdout_used_for_tuning": False,
        "blind_read": False,
        "representative_criteria": sorted(feature_tokens),
        "pages": pages,
        "inputs": {
            "stage11_exit_audit": _sha(ROOT / "data/stage11/stage11_exit_audit.json"),
            "stage11_development_selector": _sha(ROOT / "data/stage11/stage11_statement_development_samples.jsonl"),
            "stage6_evidence_bundle": _sha(ROOT / "data/stage6/stage6_evidence_bundle.jsonl"),
        },
        "consumers": ["scripts/build_stage12_candidates.py"],
    }


if __name__ == "__main__":
    result = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "page_count": len(result["pages"]), "documents": sorted({p["document_key"] for p in result["pages"]})}, ensure_ascii=False))
