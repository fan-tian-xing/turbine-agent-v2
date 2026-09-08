"""CLI for the Stage 3 synthetic fixture smoke path."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .corpus import load_corpus
from .models import ScopeContext
from .pipeline import answer_question


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Stage 3 public fixture vertical slice")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--context-json", type=Path, required=True)
    parser.add_argument("--high-risk-action", action="store_true")
    args = parser.parse_args()
    documents = load_corpus(args.corpus)
    context = ScopeContext.from_dict(json.loads(args.context_json.read_text(encoding="utf-8")))
    print(json.dumps(answer_question(documents, primary_question=args.question, context=context, high_risk_action=args.high_risk_action), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
