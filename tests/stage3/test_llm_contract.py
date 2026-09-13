import json
import copy
from dataclasses import replace
from pathlib import Path

import pytest

from turbine_kg.settings import Settings
from turbine_kg.stage3 import llm as llm_module
from turbine_kg.stage3.llm import _validate_llm_payload, generate_answer
from turbine_kg.stage3.real_trial import load_confirmed_real_corpus


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIRMATION = PROJECT_ROOT / "data" / "stage3" / "real_trial_confirmation.json"
RUNTIME_CORPUS = PROJECT_ROOT / "var" / "stage3" / "real_trial_pages.json"


def _make_hit():
    corpus = load_confirmed_real_corpus(CONFIRMATION, RUNTIME_CORPUS)
    document = corpus[0]
    statement = document.statements[0]
    evidence = document.evidence[0]
    page = document.pages[0]
    span = document.spans[0]
    return {
        "statement": {
            "id": statement.statement_id,
            "statement_type": statement.statement_type,
            "text": statement.text,
            "evidence_ids": list(statement.evidence_ids),
            "object_id": statement.object_id,
            "scope": json.dumps(statement.scope.as_dict(), ensure_ascii=False),
            "value": statement.value,
            "unit": statement.unit,
            "quantities": json.dumps(statement.quantities),
        },
        "asset": {
            "id": document.asset.asset_id,
            "relative_path": document.asset.relative_path,
            "asset_kind": document.asset.asset_kind,
            "source_root_id": document.asset.source_root_id,
            "registry_applicability_scope": list(document.asset.registry_applicability_scope),
        },
        "document": {
            "id": document.logical_document.document_logical_id,
            "title": document.title,
            "source_role": document.source_role,
            "source_scope": json.dumps(document.source_scope.as_dict(), ensure_ascii=False),
        },
        "revision": {"id": document.revision.revision_id, "label": document.revision.label},
        "sources": [{
            "evidence": {
                "id": evidence.evidence_id,
                "source_span_ids": list(evidence.source_span_ids),
                "statement_id": evidence.statement_id,
                "object_id": evidence.object_id,
                "text": evidence.text,
                "value": evidence.value,
                "unit": evidence.unit,
                "quantities": json.dumps(evidence.quantities),
            },
            "span": {"id": span.span_id, "quote": span.quote},
            "page": {
                "id": page.page_id,
                "page_number": page.page_number,
                "physical_page": page.page_number,
                "logical_page": page.logical_page,
            },
        }],
    }


def test_evidence_payload_omits_local_relative_path() -> None:
    hit = _make_hit()
    from turbine_kg.stage3.llm import _evidence_payload
    payload = json.dumps(_evidence_payload([hit]), ensure_ascii=False)
    assert "relative_path" not in payload
    assert hit["asset"]["relative_path"] not in payload


def _valid_payload(hit, *, context_key="context", claim_type="fact"):
    statement = hit["statement"]
    evidence_id = statement["evidence_ids"][0]
    claim = {
        "claim_id": "llm-claim-1",
        "claim_type": claim_type,
        "text": statement["text"],
        "statement_id": statement["id"],
        "evidence_ids": [evidence_id],
        "physical_page": 86,
        "logical_page": "75",
        "object_id": statement["object_id"],
        context_key: json.loads(statement["scope"]),
        "value": statement["value"],
        "unit": statement["unit"],
        "quantities": [],
    }
    return {
        "answer": f'{statement["text"]} 依据：{evidence_id}',
        "claims": [claim],
    }


def test_llm_payload_is_claim_validated_with_page_and_evidence():
    hit = _make_hit()
    payload = _valid_payload(hit)
    payload["answer"] = "这是模型草稿中的未绑定断言。"
    answer, count, status = _validate_llm_payload(payload, [hit])
    assert answer.startswith(hit["statement"]["text"])
    assert "未绑定断言" not in answer
    assert hit["statement"]["evidence_ids"][0] in answer
    assert f"原文（{hit['statement']['evidence_ids'][0]}）：{hit['sources'][0]['evidence']['text']}" in answer
    assert count == 1
    assert status == "passed"


def test_llm_payload_rejects_wrong_page_and_object():
    hit = _make_hit()
    payload = _valid_payload(hit)
    payload["claims"][0]["physical_page"] = 999
    payload["claims"][0]["object_id"] = "wrong-object"
    with pytest.raises(ValueError, match="page"):
        _validate_llm_payload(payload, [hit])


def test_llm_payload_rejects_missing_physical_page_and_wrong_logical_page():
    hit = _make_hit()
    payload = _valid_payload(hit)
    payload["claims"][0].pop("physical_page")
    with pytest.raises(ValueError, match="page location"):
        _validate_llm_payload(payload, [hit])


def test_llm_payload_rejects_legacy_page_field():
    hit = _make_hit()
    payload = _valid_payload(hit)
    payload["claims"][0]["page"] = 86
    with pytest.raises(ValueError, match="legacy page field"):
        _validate_llm_payload(payload, [hit])


def test_llm_payload_requires_all_locations_for_multi_page_evidence():
    hit = _make_hit()
    extra = copy.deepcopy(hit["sources"][0])
    extra["evidence"]["id"] = "real-evidence-dl5190-p87-extra"
    extra["page"]["id"] = "page-real-dl5190-p87"
    extra["page"]["page_number"] = 87
    extra["page"]["physical_page"] = 87
    extra["page"]["logical_page"] = None
    hit["sources"].append(extra)
    hit["statement"]["evidence_ids"].append(extra["evidence"]["id"])
    payload = _valid_payload(hit)
    payload["claims"][0]["evidence_ids"] = hit["statement"]["evidence_ids"]
    with pytest.raises(ValueError, match="source_locations"):
        _validate_llm_payload(payload, [hit])
    payload["claims"][0]["source_locations"] = [
        {"physical_page": 86, "logical_page": "75"},
        {"physical_page": 87, "logical_page": None},
    ]
    answer, _, _ = _validate_llm_payload(payload, [hit])
    assert "物理页第86页" in answer
    assert "物理页第87页" in answer
    payload = _valid_payload(hit)
    payload["claims"][0]["logical_page"] = "wrong"
    with pytest.raises(ValueError, match="page location"):
        _validate_llm_payload(payload, [hit])


def test_llm_payload_accepts_applicability_alias_with_same_strict_validation():
    hit = _make_hit()
    payload = _valid_payload(hit, context_key="applicability")
    answer, count, status = _validate_llm_payload(payload, [hit])
    assert answer.startswith(hit["statement"]["text"])
    assert count == 1
    assert status == "passed"


def test_action_authorization_is_rendered_as_a_non_executable_candidate():
    hit = _make_hit()
    answer, count, status = _validate_llm_payload(
        _valid_payload(hit, claim_type="action_authorization"), [hit]
    )
    assert count == 1
    assert status == "downgraded_candidate"
    assert "not_authorized_for_execution" in answer
    assert "不得作为现场执行授权" in answer
    assert "物理页第86页" in answer
    assert "逻辑页75" in answer


def test_generate_answer_enforces_structured_claim_contract_without_external_network(monkeypatch):
    hit = _make_hit()
    response_payload = _valid_payload(hit)
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": json.dumps(response_payload, ensure_ascii=False)}}],
            }).encode("utf-8")

    monkeypatch.setattr(llm_module, "urlopen", lambda request, timeout: FakeResponse())
    settings = replace(
        Settings.from_environment(dotenv_path=PROJECT_ROOT / ".env.test-not-used"),
        llm_base_url="http://local.test",
        llm_model="local-contract-test",
        llm_api_key="local-only",
        llm_allow_evidence_send=True,
    )
    result = generate_answer("塞尺检查要求是什么？", [hit], settings)
    assert result.ok is True
    assert result.claim_count == 1
    assert result.claim_validation == "passed"


def test_claim_validation_error_does_not_call_fallback(monkeypatch):
    hit = _make_hit()
    invalid = _valid_payload(hit)
    invalid["claims"][0].pop("context")
    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            calls.append("primary")
            return json.dumps({
                "choices": [{"message": {"content": json.dumps(invalid, ensure_ascii=False)}}],
            }).encode("utf-8")

    monkeypatch.setattr(llm_module, "urlopen", lambda request, timeout: FakeResponse())
    settings = replace(
        Settings.from_environment(dotenv_path=PROJECT_ROOT / ".env.test-not-used"),
        llm_base_url="http://primary.test",
        llm_model="primary",
        llm_api_key="local-only",
        llm_fallback_base_url="http://fallback.test",
        llm_fallback_model="fallback",
        llm_fallback_api_key="local-only",
        llm_allow_evidence_send=True,
    )
    result = generate_answer("塞尺检查要求是什么？", [hit], settings)
    assert result.ok is False
    assert "context" in result.error
    assert calls == ["primary"]


def test_model_answer_draft_never_reaches_the_user_visible_answer():
    hit = _make_hit()
    payload = _valid_payload(hit)
    payload["answer"] += " 另见 real-evidence-dl5190-p86-contact 和未验证动作。"
    answer, _, _ = _validate_llm_payload(payload, [hit])
    assert "real-evidence-dl5190-p86-contact" not in answer
    assert "未验证动作" not in answer
