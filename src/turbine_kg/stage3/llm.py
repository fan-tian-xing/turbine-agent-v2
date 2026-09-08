"""Small OpenAI-compatible LLM adapter for the local Stage 3 trial."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from turbine_kg.settings import Settings
from .models import Claim, ScopeContext
from .validation import ALLOWED_CLAIM_TYPES, validate_claim


@dataclass(frozen=True, slots=True)
class LLMResult:
    ok: bool
    text: str = ""
    endpoint: str = ""
    model: str = ""
    error: str = ""
    claim_count: int = 0
    claim_validation: str = ""


def _endpoint_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def _evidence_payload(hits: list[dict]) -> list[dict]:
    payload = []
    for hit in hits:
        statement = hit["statement"]
        sources = hit["sources"]
        payload.append(
            {
                "statement_id": statement["id"],
                "statement": statement["text"],
                "object_id": statement["object_id"],
                "value": statement.get("value"),
                "unit": statement.get("unit"),
                "applicability": json.loads(statement["scope"]),
                "source": [
                    {
                        "evidence_id": item["evidence"]["id"],
                        "quote": item["evidence"]["text"],
                        "page": item["page"].get("page_number"),
                        "title": hit["document"]["title"],
                        "relative_path": hit["asset"]["relative_path"],
                    }
                    for item in sources
                ],
                "retrieval_condition": hit.get("applicability"),
                "missing_context": hit.get("missing_context", []),
            }
        )
    return payload


def _prompt(question: str, hits: list[dict]) -> tuple[str, str]:
    system = (
        "你是证据约束型汽轮机安装调试技术助手。只能依据用户问题和 EVIDENCE_JSON 回答。"
        "不得补造数字、单位、比较符、机型、页码或操作授权。若资料之间冲突，明确说存在冲突并列出各自依据；"
        "若条件不足，明确指出缺少的条件；涉及起吊、调整、打磨、更换或执行动作时，只能给候选建议，不能授权现场执行。"
        "回答使用中文。必须只返回一个 JSON 对象，不要 Markdown、代码围栏或额外说明。JSON 必须包含 answer 和 claims。"
        "answer 是给用户看的中文回答；claims 是原子结论数组。每个 claim 必须包含：claim_id、claim_type、text、"
        "statement_id、evidence_ids、page、object_id、context、value、unit、quantities。claim_type 只能是 fact、"
        "conditioned_inference、candidate_recommendation、action_authorization。context 必须复制该结论实际使用的适用条件；"
        "如果使用 applicability 作为同义字段名，也必须提供同样的 JSON 对象，不能省略。"
        "每个 claim 的 evidence_ids 必须来自 EVIDENCE_JSON，并且 answer 中必须原样出现这些 evidence_id。"
        "answer 中不得出现 claims.evidence_ids 之外的 Evidence ID；如果 answer 使用两条证据，必须分别在 claims 中列出并绑定。"
    )
    user = "问题：\n" + question + "\n\nEVIDENCE_JSON：\n" + json.dumps(_evidence_payload(hits), ensure_ascii=False, indent=2)
    return system, user


def _parse_json_response(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE | re.DOTALL).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM response is not a JSON object")
    payload = json.loads(cleaned[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("LLM response JSON must be an object")
    return payload


def _validate_llm_payload(payload: dict, hits: list[dict]) -> tuple[str, int, str]:
    answer = payload.get("answer")
    claims = payload.get("claims")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("LLM response answer is missing")
    if not isinstance(claims, list) or not claims:
        raise ValueError("LLM response claims are missing")

    hit_by_statement = {hit["statement"]["id"]: hit for hit in hits}
    allowed_evidence = {
        item["evidence"]["id"]
        for hit in hits
        for item in hit["sources"]
    }
    validated_claims: list[tuple[dict, object]] = []
    claim_statuses: set[str] = set()
    for raw in claims:
        if not isinstance(raw, dict):
            raise ValueError("each LLM claim must be an object")
        if not raw.get("claim_id") or not isinstance(raw.get("text"), str) or not raw["text"].strip():
            raise ValueError("each LLM claim needs claim_id and text")
        claim_type = raw.get("claim_type")
        statement_id = raw.get("statement_id")
        evidence_ids = raw.get("evidence_ids")
        if claim_type not in ALLOWED_CLAIM_TYPES:
            raise ValueError(f"unsupported LLM claim type: {claim_type}")
        if statement_id not in hit_by_statement:
            raise ValueError(f"LLM claim references an unreturned statement: {statement_id}")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            raise ValueError("LLM claim must reference Evidence")
        hit = hit_by_statement[statement_id]
        statement = hit["statement"]
        statement_evidence = set(statement["evidence_ids"])
        if not set(evidence_ids) <= statement_evidence or not set(evidence_ids) <= allowed_evidence:
            raise ValueError(f"LLM claim references unsupported Evidence: {evidence_ids}")
        source_pages = {
            item["page"].get("page_number")
            for item in hit["sources"]
            if item["evidence"]["id"] in evidence_ids
        }
        if raw.get("page") not in source_pages:
            raise ValueError(f"LLM claim page is not supported for {statement_id}")
        if raw.get("object_id") != statement["object_id"]:
            raise ValueError(f"LLM claim object mismatch for {statement_id}")
        # Some OpenAI-compatible models use the source vocabulary
        # "applicability" even when the prompt asks for "context".  Accept
        # that alias, but keep the same strict object-level validation.
        context = raw.get("context", raw.get("applicability"))
        if not isinstance(context, dict):
            raise ValueError(f"LLM claim context is missing for {statement_id}")
        raw_quantities = raw.get("quantities", [])
        if not isinstance(raw_quantities, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("unit"), str)
            for item in raw_quantities
        ):
            raise ValueError(f"LLM claim quantities are malformed for {statement_id}")
        quantities = tuple((item.get("min"), item.get("max"), item["unit"]) for item in raw_quantities)
        claim = Claim(
            claim_id=str(raw.get("claim_id", "")),
            claim_type=claim_type,
            text=str(raw.get("text", "")),
            statement_id=statement_id,
            evidence_ids=tuple(evidence_ids),
            object_id=raw["object_id"],
            context=ScopeContext.from_dict(context),
            value=raw.get("value"),
            unit=raw.get("unit"),
            quantities=quantities,
        )
        from .neo4j_trial import hit_document

        result = validate_claim(claim, (hit_document(hit),))
        if not result.allowed:
            raise ValueError(f"LLM claim validation failed for {statement_id}: {result.failures}")
        claim_statuses.add(result.status)
        validated_claims.append((raw, result))
    rendered_claims = []
    for raw, result in validated_claims:
        evidence_text = "、".join(raw["evidence_ids"])
        rendered = f"{raw['text']}（依据：{evidence_text}；第{raw['page']}页）"
        if result.status == "downgraded_candidate":
            rendered += "；not_authorized_for_execution：不得作为现场执行授权。"
        rendered_claims.append(rendered)
    # The model's `answer` is treated as an untrusted draft.  The user-facing
    # answer is rendered solely from claims that have passed local validation.
    answer = "\n".join(rendered_claims)
    status = "downgraded_candidate" if "downgraded_candidate" in claim_statuses else "passed"
    return answer.strip(), len(claims), status


def generate_answer(question: str, hits: list[dict], settings: Settings) -> LLMResult:
    if not hits:
        return LLMResult(False, error="no applicable evidence was retrieved")
    if not settings.llm_allow_evidence_send:
        return LLMResult(False, error="LLM_ALLOW_EVIDENCE_SEND is false")
    endpoints = [
        (settings.llm_base_url, settings.llm_model, settings.llm_api_key, settings.llm_timeout_seconds),
        (settings.llm_fallback_base_url, settings.llm_fallback_model, settings.llm_fallback_api_key, settings.llm_fallback_timeout_seconds),
        (settings.llm_fallback_2_base_url, settings.llm_fallback_2_model, settings.llm_fallback_2_api_key, settings.llm_fallback_2_timeout_seconds),
    ]
    system, user = _prompt(question, hits)
    failures = []
    for base_url, model, api_key, timeout in endpoints:
        if not base_url or not model or not api_key:
            continue
        body = json.dumps(
            {
                "model": model,
                "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            _endpoint_url(base_url),
            data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            raw_text = payload["choices"][0]["message"]["content"]
            if not isinstance(raw_text, str) or not raw_text.strip():
                raise ValueError("LLM returned empty content")
            answer_payload = _parse_json_response(raw_text)
            text, claim_count, claim_validation = _validate_llm_payload(answer_payload, hits)
            return LLMResult(
                True,
                text=text,
                endpoint=base_url,
                model=model,
                claim_count=claim_count,
                claim_validation=claim_validation,
            )
        except (HTTPError, URLError, TimeoutError) as error:
            failures.append(f"{type(error).__name__}: {error}")
            continue
        except (ValueError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as error:
            # The service responded, but its content failed the local contract.
            # Do not hide a semantic/Claim defect behind a fallback model.
            return LLMResult(False, endpoint=base_url, model=model, error=f"{type(error).__name__}: {error}")
    return LLMResult(False, error="; ".join(failures) or "no usable LLM endpoint configured")
