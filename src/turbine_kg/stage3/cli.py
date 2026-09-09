"""CLI for the Stage 3 synthetic fixture smoke path."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from turbine_kg.documents.vocabulary import display_name, load_display_terms
from turbine_kg.settings import PROJECT_ROOT

from .corpus import load_corpus
from .models import ScopeContext
from .pipeline import answer_question


def _with_chinese_display_names(result: dict) -> dict:
    """Keep machine identifiers while making the CLI's displayed labels Chinese."""
    terms = load_display_terms(PROJECT_ROOT / "config" / "term_display_map.json")
    for claim in result.get("claims", []):
        claim["claim_type_display_name"] = display_name(claim["claim_type"], terms)
    retrieval = result.get("retrieval")
    if isinstance(retrieval, dict) and isinstance(retrieval.get("source_role"), str):
        retrieval["source_role_display_name"] = display_name(retrieval["source_role"], terms)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Stage 3 public fixture vertical slice")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--context-json", type=Path, required=True)
    parser.add_argument("--high-risk-action", action="store_true")
    args = parser.parse_args()
    documents = load_corpus(args.corpus)
    context = ScopeContext.from_dict(json.loads(args.context_json.read_text(encoding="utf-8")))
    result = answer_question(documents, primary_question=args.question, context=context, high_risk_action=args.high_risk_action)
    print(json.dumps(_with_chinese_display_names(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
