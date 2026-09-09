"""Local Neo4j + Chinese question + LLM trial CLI."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from pathlib import Path

from neo4j.exceptions import Neo4jError, ServiceUnavailable

from turbine_kg.settings import PROJECT_ROOT, Settings
from .llm import generate_answer
from .models import ScopeContext
from .neo4j_trial import TrialGraph


_CONTEXT_LABELS = {
    "equipment": "设备",
    "lifecycle_stage": "生命周期阶段",
    "activity": "活动",
    "condition": "工况",
    "model": "机型",
    "operating_state": "运行状态",
    "capacity_range": "容量范围",
}

# The CLI reports a concise connection diagnostic itself.  Suppress the
# driver's duplicate routing warning so an unavailable local database does not
# look like an unhandled traceback to users.
logging.getLogger("neo4j").setLevel(logging.CRITICAL)


def _settings(args) -> Settings:
    settings = Settings.from_environment(
        dotenv_path=PROJECT_ROOT / ".env",
    )
    if args.no_evidence_send:
        settings = replace(settings, llm_allow_evidence_send=False)
    elif args.allow_evidence_send:
        settings = replace(settings, llm_allow_evidence_send=True)
    return settings


def _human_result(result: dict) -> None:
    """Print a concise Chinese answer while keeping JSON available for tools."""
    print(f"问题：{result['primary_question']}")
    if result["status"] == "evidence_gap":
        print("\n回答：")
        print("未检索到可用的工程证据，暂不能给出有依据的结论。")
        return

    llm = result.get("llm") or {}
    print("\n回答：")
    if llm.get("ok"):
        print(llm.get("text", "").strip())
    elif llm.get("skipped"):
        print("已按无模型模式跳过 LLM，仅显示检索依据。")
    else:
        print("模型暂未返回组织后的回答。")
        if llm.get("error"):
            print(f"原因：{llm['error']}")

    print("\n检索依据：")
    for hit in result["hits"]:
        evidence_ids = "、".join(hit["evidence_ids"]) or "未提供 Evidence ID"
        physical_page = hit.get("physical_page", hit.get("page"))
        page = f"物理页第{physical_page}页" if physical_page else "物理页未确认"
        if hit.get("logical_page") is not None:
            page += f"；逻辑页{hit['logical_page']}"
        print(f"- {hit['statement']}（{hit['title']}，{page}；{evidence_ids}）")

    missing: list[str] = []
    applicability: list[str] = []
    for hit in result["hits"]:
        if hit.get("applicability"):
            applicability.append(hit["applicability"])
        for item in hit.get("missing_context", []):
            key = item.split(":", 1)[1] if ":" in item else item
            label = _CONTEXT_LABELS.get(key, key)
            if label not in missing:
                missing.append(label)
    if missing:
        print("\n适用性提示：条件参考；尚缺少 " + "、".join(missing) + " 信息。")
    elif applicability:
            print("\n适用性：已按当前问题条件匹配。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local Stage 3 Neo4j and LLM trial")
    parser.add_argument(
        "command_or_question",
        nargs="?",
        help="import, status, ask, or a question directly (old CLI-compatible form)",
    )
    parser.add_argument("question_positional", nargs="?", help="one Chinese primary question")
    parser.add_argument("--question", help="one Chinese primary question (compatibility form)")
    parser.add_argument("--context-json", type=Path, help="JSON file containing model/equipment/lifecycle/activity/condition")
    parser.add_argument("--json", action="store_true", help="print the complete machine-readable JSON result")
    parser.add_argument("--allow-evidence-send", action="store_true", default=False, help=argparse.SUPPRESS)
    parser.add_argument("--no-evidence-send", action="store_true", help="do not send retrieved evidence to the LLM")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    settings = _settings(args)
    command = args.command_or_question if args.command_or_question in {"import", "status", "ask"} else "ask"
    direct_question = None if command != "ask" else (
        args.question_positional if args.command_or_question == "ask" else args.command_or_question
    )
    question = args.question or direct_question
    if command == "ask" and not question:
        question = input("请输入工程问题：").strip()
        if not question:
            parser.error("问题不能为空")

    try:
        with TrialGraph(settings) as graph:
            if command == "import":
                print(json.dumps(graph.import_confirmed(), ensure_ascii=False, indent=2))
                return 0
            if command == "status":
                print(json.dumps(graph.status(), ensure_ascii=False, indent=2))
                return 0
            context = ScopeContext()
            if args.context_json:
                context = ScopeContext.from_dict(json.loads(args.context_json.read_text(encoding="utf-8")))
            hits = graph.retrieve(question, context)
            result = {
                "status": "evidence_gap" if not hits else "retrieved",
                "primary_question": question,
                "hits": [
                    {
                        "statement_id": hit["statement"]["id"],
                        "statement": hit["statement"]["text"],
                        "title": hit["document"]["title"],
                        "physical_page": (
                            hit["sources"][0]["page"].get(
                                "physical_page", hit["sources"][0]["page"].get("page_number")
                            )
                            if hit["sources"] else None
                        ),
                        "logical_page": (
                            hit["sources"][0]["page"].get("logical_page")
                            if hit["sources"] else None
                        ),
                        "evidence_ids": [item["evidence"]["id"] for item in hit["sources"]],
                        "applicability": hit["applicability"],
                        "missing_context": hit["missing_context"],
                    }
                    for hit in hits
                ],
            }
            if hits and not args.no_evidence_send:
                llm = generate_answer(question, hits, settings)
                result["llm"] = {
                    "ok": llm.ok,
                    "text": llm.text,
                    "model": llm.model,
                    "error": llm.error,
                    "claim_count": llm.claim_count,
                    "claim_validation": llm.claim_validation,
                }
            elif hits:
                result["llm"] = {"ok": False, "skipped": True, "error": "no-evidence-send"}
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                _human_result(result)
            return 0 if hits else 2
    except (ServiceUnavailable, Neo4jError, OSError) as exc:
        print(f"Neo4j 连接失败：{exc}", file=sys.stderr)
        print("请先启动新版项目的 Neo4j（默认端口 7688），或检查 NEO4J_URI。", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
