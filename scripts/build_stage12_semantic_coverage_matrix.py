"""Build the deterministic Stage 12 semantic-coverage summary from current Gold."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = ROOT / "data/stage11/stage11_statement_development_samples.jsonl"
OUTPUT_PATH = ROOT / "data/stage12/stage12_semantic_coverage_matrix.json"
GENERATOR_VERSION = "stage12-semantic-coverage-v2"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values if value is not None).items()))


def build_matrix(
    gold_path: Path = GOLD_PATH,
    generator_path: Path = Path(__file__),
) -> dict[str, Any]:
    rows = _jsonl(gold_path)
    quantity_rows = [quantity for row in rows for quantity in row.get("quantities", [])]
    scope_rows = [row.get("applicability_scope") or {} for row in rows]
    operator_counts = _counts(quantity.get("operator") for quantity in quantity_rows)
    gaps = ["gold_not_exhaustive"]
    if not {"lt", "lte"} <= set(operator_counts):
        gaps.append("comparison_operators_lt_lte_not_represented")

    return {
        "schema_version": 2,
        "stage": "12",
        "artifact_kind": "stage12_semantic_coverage_matrix",
        "status": "current",
        "producer": "scripts/build_stage12_semantic_coverage_matrix.py",
        "generator_version": GENERATOR_VERSION,
        "generator_sha256": _sha(generator_path),
        "gold_artifact": "data/stage11/stage11_statement_development_samples.jsonl",
        "gold_sha256": _sha(gold_path),
        "gold_statement_count": len(rows),
        "gold_exhaustive": False,
        "coverage": {
            "documents": {
                "count": len({row.get("document_key") for row in rows if row.get("document_key")}),
                "values": sorted({row.get("document_key") for row in rows if row.get("document_key")}),
            },
            "statement_type": _counts(row.get("statement_type") for row in rows),
            "predicate": _counts(row.get("predicate") for row in rows),
            "normative_modality": _counts(row.get("normative_modality") for row in rows),
            "quantity": {
                "statement_count": sum(bool(row.get("quantities")) for row in rows),
                "quantity_count": len(quantity_rows),
                "operator_counts": operator_counts,
                "unit_counts": _counts(quantity.get("unit") for quantity in quantity_rows),
            },
            "negation": {
                "statement_count": sum(bool(row.get("negation_scope")) for row in rows),
            },
            "condition": {
                "statement_count": sum(bool(row.get("conditions")) for row in rows),
            },
            "applicability": {
                "statement_count": sum(bool(scope) for scope in scope_rows),
                "scope_key_counts": _counts(key for scope in scope_rows for key in scope),
                "status_counts": _counts(scope.get("status") for scope in scope_rows),
            },
            "entity": {
                "multi_entity_statement_count": sum(len(row.get("entity_alignment", [])) > 1 for row in rows),
                "maximum_entities_per_statement": max((len(row.get("entity_alignment", [])) for row in rows), default=0),
            },
        },
        "gaps": gaps,
        "coverage_claim": "descriptive_development_gold_coverage_only",
        "consumer": "scripts/audit_stage12_exit.py",
    }


def write_matrix(output_path: Path = OUTPUT_PATH) -> dict[str, Any]:
    matrix = build_matrix()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    json.loads(temporary_path.read_text(encoding="utf-8"))
    temporary_path.replace(output_path)
    return matrix


if __name__ == "__main__":
    result = write_matrix()
    print(json.dumps({"status": result["status"], "gold_statement_count": result["gold_statement_count"]}))
