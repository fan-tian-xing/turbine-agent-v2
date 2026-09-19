import json
import socket
import threading
import time
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from turbine_kg.extraction.semantic import (
    ExtractionProfile,
    ExtractionProviderError,
    ExternalLLMProvider,
    FailoverExternalLLMProvider,
    FixtureExtractionProvider,
)
from turbine_kg.llm_client import LLMTransportError, OpenAICompatibleChatTransport


ROOT = Path(__file__).resolve().parents[2]


def _evidence(text="真空不得低于60kPa。"):
    return {
        "evidence_id": "failover-evidence",
        "document_logical_id": "failover-document",
        "revision_id": "failover-revision",
        "physical_page": 1,
        "source_span_id": "failover-span",
        "source_text_sha256": __import__("hashlib").sha256(text.encode()).hexdigest(),
        "source_text": text,
        "review_status": "accepted",
        "document_key": "fixture",
    }


def _profile():
    return ExtractionProfile(
        semantic_role="stage12_failover",
        extraction_profile_id="stage12_failover_test_v1",
        source_profile_id="stage12_failover_test",
        source_applicability_scope=(),
        external_llm_allowed=True,
    )


def _valid_response():
    return json.dumps(FixtureExtractionProvider().extract(_evidence(), _profile()), ensure_ascii=False)


def _provider(transport, alias, fingerprint):
    return ExternalLLMProvider(
        transport=transport,
        model_config_identifier=fingerprint,
        max_attempts=1,
        provider_alias=alias,
        endpoint_alias=alias,
    )


def _failover(primary, backup):
    return FailoverExternalLLMProvider(primary, backup, policy_fingerprint="policy-test")


def test_primary_success_never_calls_backup_and_preserves_primary_provenance():
    backup_calls = []
    provider = _failover(
        _provider(lambda prompt: _valid_response(), "primary", "primary-fp"),
        _provider(lambda prompt: backup_calls.append(prompt) or _valid_response(), "backup", "backup-fp"),
    )

    response = provider.extract(_evidence(), _profile())

    assert response["provider_metadata"]["provider_alias"] == "primary"
    assert response["provider_metadata"]["generated_by"] == "primary"
    assert response["provider_metadata"].get("failover_used") is not True
    assert backup_calls == []
    assert provider.metadata["fallback_count"] == 0


@pytest.mark.parametrize(
    "root_cause",
    ["dns_resolution", "read_timeout", "http_503"],
)
def test_availability_failures_switch_to_backup(root_cause):
    backup_calls = []

    def primary_transport(prompt):
        raise LLMTransportError(
            "temporary endpoint failure",
            root_cause=root_cause,
            status_code=503 if root_cause == "http_503" else None,
            fallback_eligible=True,
            attempts=1,
        )

    provider = _failover(
        _provider(primary_transport, "primary", "primary-fp"),
        _provider(lambda prompt: backup_calls.append(prompt) or _valid_response(), "backup", "backup-fp"),
    )
    events = []

    response = provider.extract(_evidence(), _profile(), attempt_observer=events.append)

    metadata = response["provider_metadata"]
    assert backup_calls
    assert metadata["provider_alias"] == "backup"
    assert metadata["generated_by"] == "backup"
    assert metadata["failover_used"] is True
    assert metadata["fallback_from"] == "primary"
    assert provider.metadata["fallback_count"] == 1
    assert any(event["fallback_triggered"] and event["fallback_provider"] == "backup" for event in events)


def test_transport_maps_url_error_timeout_and_503_without_waiting_full_retry_budget():
    errors = [
        URLError(socket.gaierror("no such host")),
        TimeoutError("read timeout"),
        HTTPError("https://example.invalid", 503, "busy", Message(), None),
    ]
    for error in errors:
        sleeps = []

        def opener(request, timeout, error=error):
            raise error

        transport = OpenAICompatibleChatTransport(
            endpoint="https://example.invalid/v1",
            model="test-model",
            api_key="test-secret",
            timeout_seconds=180,
            max_attempts=3,
            transient_max_attempts=2,
            timeout_max_attempts=1,
            sleeper=sleeps.append,
            opener=opener,
        )
        with pytest.raises(LLMTransportError) as raised:
            transport({"system": "system", "user": "user"})
        assert raised.value.fallback_eligible is True
        assert raised.value.attempts <= 2
        assert len(sleeps) <= 1


def test_transport_deadline_bounds_a_stalled_opener():
    stalled = threading.Event()

    def opener(request, timeout):
        stalled.wait(10)

    transport = OpenAICompatibleChatTransport(
        endpoint="https://example.invalid/v1",
        model="test-model",
        api_key="test-secret",
        timeout_seconds=0.05,
        max_attempts=1,
        opener=opener,
    )
    started = time.monotonic()
    with pytest.raises(LLMTransportError, match="transport failure"):
        transport({"system": "system", "user": "user"})
    assert time.monotonic() - started < 1.0


def test_non_availability_errors_do_not_failover_and_both_failure_isolated():
    backup_calls = []
    primary = _provider(
        lambda prompt: (_ for _ in ()).throw(LLMTransportError("bad request", root_cause="http_400", status_code=400)),
        "primary",
        "primary-fp",
    )
    backup = _provider(lambda prompt: backup_calls.append(prompt) or _valid_response(), "backup", "backup-fp")
    with pytest.raises(ExtractionProviderError) as raised:
        _failover(primary, backup).extract(_evidence(), _profile())
    assert backup_calls == []
    assert raised.value.fallback_eligible is False

    both_fail = _failover(
        _provider(lambda prompt: (_ for _ in ()).throw(LLMTransportError("primary unavailable", root_cause="connection_refused", fallback_eligible=True)), "primary", "primary-fp"),
        _provider(lambda prompt: (_ for _ in ()).throw(LLMTransportError("backup unavailable", root_cause="http_503", status_code=503, fallback_eligible=True)), "backup", "backup-fp"),
    )
    with pytest.raises(ExtractionProviderError) as failed:
        both_fail.extract(_evidence(), _profile())
    assert failed.value.details["primary"]["root_cause"] == "connection_refused"
    assert failed.value.details["backup"]["root_cause"] == "http_503"
    assert "test-secret" not in json.dumps(failed.value.details)


def test_backup_cache_provenance_is_accepted_but_not_relabelled():
    provider = _failover(
        _provider(lambda prompt: (_ for _ in ()).throw(LLMTransportError("down", root_cause="connection_refused", fallback_eligible=True)), "primary", "primary-fp"),
        _provider(lambda prompt: _valid_response(), "backup", "backup-fp"),
    )
    response = provider.extract(_evidence(), _profile())
    cached = {"provider_metadata": response["provider_metadata"]}
    assert provider.cache_provider_matches(cached)
    assert cached["provider_metadata"]["config_fingerprint"] == "backup-fp"
    assert cached["provider_metadata"]["provider_alias"] == "backup"
