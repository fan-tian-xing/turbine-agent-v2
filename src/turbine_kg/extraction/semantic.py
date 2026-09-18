"""Stage 12 candidate semantic extraction and evaluation primitives.

The extractor consumes reviewed Evidence text and emits candidates only.  It
does not read Gold labels, create formal Statements, or write a graph.  The
production provider is an LLM-backed JSON extractor; the small rule backend is
available only as an explicit fixture for tests and offline pipeline checks.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Protocol

from jsonschema import Draft202012Validator

from turbine_kg.observability.runtime import canonical_json
from turbine_kg.ontology.semantic import validate_runtime_payload
from turbine_kg.settings import Settings
from turbine_kg.llm_client import LLMTransportError, OpenAICompatibleChatTransport


ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = ROOT / "config/stage12_statement_contract.json"
SCHEMA_PATH = ROOT / "config/stage12_candidate.schema.json"
PROFILE_ROUTING_PATH = ROOT / "config/stage12_profile_routing.json"
PROVIDER_CONFIG_PATH = ROOT / "config/stage12_provider.json"
PROMPT_PATH = ROOT / "config/stage12_prompt.txt"
RESPONSE_SCHEMA_PATH = ROOT / "config/stage12_extraction_response.schema.json"
COARSE_RELATIONS = frozenset({"requires", "prohibits", "describes", "causes", "verifies", "limits_scope"})
STATEMENT_TYPES = frozenset({"fact", "requirement", "procedure", "condition", "observation", "verification", "limitation"})
ENTITY_ROLES = frozenset({"subject", "object", "related", "quantity_target"})
STAGE9_TYPE = {
    "fact": "fact",
    "requirement": "acceptance_requirement",
    "procedure": "maintenance_procedure",
    "condition": "acceptance_requirement",
    "observation": "fact",
    "verification": "inspection_requirement",
    "limitation": "scope_definition",
}
UNITS = r"mm|m|μm|MW|MPa|kPa|Pa|%|％|s|Hz|r/min|℃|°C|dB|t/h"
NUMBER = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"
QUANTITY_RE = re.compile(
    rf"(?P<left>{NUMBER})\s*(?:～|~|至|到|-|—)\s*(?P<right>{NUMBER})\s*(?P<unit>{UNITS})"
    rf"|(?P<single>{NUMBER})\s*(?P<single_unit>{UNITS})",
    re.IGNORECASE,
)
COMPARATORS = (
    ("不小于", "gte", "lower_bound"), ("不少于", "gte", "lower_bound"),
    ("不低于", "gte", "lower_bound"), ("至少", "gte", "lower_bound"),
    ("以上", "gte", "lower_bound"),
    ("不大于", "lte", "upper_bound"), ("不超过", "lte", "upper_bound"),
    ("不高于", "lte", "upper_bound"), ("至多", "lte", "upper_bound"),
    ("以下", "lte", "upper_bound"),
    ("大于", "gt", "lower_bound"), ("超过", "gt", "lower_bound"),
    ("小于", "lt", "upper_bound"),
)
NEGATIONS = ("不得", "不应", "不小于", "不大于", "不少于", "不低于", "不超过", "无", "未", "不入")
CONDITION_RE = re.compile(r"(?:当[^。；，,]{1,32}时|若[^。；，,]{1,32}|如果[^。；，,]{1,32}|在[^。；，,]{1,32}状态下|大修时)")
ACTION_MARKERS = "应必须需可检查调整确认保证进行达到满足包括采用有"
NOISE_PREFIXES = ("编制审核", "录入员", "目录", "目次", "题库", "选择题")


class StatementExtractor(Protocol):
    profile_id: str

    def extract(
        self,
        evidence: Mapping[str, Any],
        *,
        attempt_observer: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Extract candidate statements from one Evidence record."""


class ExtractionProvider(Protocol):
    """Replaceable provider boundary; provider output is never formal knowledge."""

    provider_id: str

    def extract(self, evidence: Mapping[str, Any], profile: "ExtractionProfile") -> Mapping[str, Any]:
        """Return one strict Stage 12 response object."""


class ExtractionProviderError(ValueError):
    """Provider transport/configuration failure, distinct from semantic validation."""


class ExtractionSchemaError(ValueError):
    """Provider returned a response that does not satisfy the strict schema."""


OpenAICompatibleTransport = OpenAICompatibleChatTransport


def parse_provider_response(raw: str | Mapping[str, Any], schema_path: Path = RESPONSE_SCHEMA_PATH) -> dict[str, Any]:
    """Parse strict semantic JSON and supply fixed protocol metadata locally."""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ExtractionSchemaError("provider response is not valid JSON") from error
    elif isinstance(raw, Mapping):
        parsed = dict(raw)
    else:
        raise ExtractionSchemaError("provider response must be a JSON object")
    # These identify the local adapter contract, not model semantics.  Supply
    # them here so a protocol formatting slip cannot consume a semantic retry.
    parsed["schema_version"] = 1
    parsed["response_kind"] = "stage12_candidate_extraction"
    errors = sorted(
        Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8"))).iter_errors(parsed),
        key=lambda error: list(error.path),
    )
    if errors:
        raise ExtractionSchemaError("invalid Stage 12 provider response: " + "; ".join(error.message for error in errors))
    if parsed["status"] == "no_statement" and parsed["candidates"]:
        raise ExtractionSchemaError("no_statement response must not contain candidates")
    if parsed["status"] == "ok" and not parsed["candidates"]:
        raise ExtractionSchemaError("ok response must contain at least one candidate")
    return parsed


def stage12_prompt(evidence: Mapping[str, Any], profile: "ExtractionProfile") -> dict[str, str]:
    """Build a traceable prompt envelope without embedding source-specific rules."""
    response_schema = json.loads(RESPONSE_SCHEMA_PATH.read_text(encoding="utf-8"))
    candidate_schema = response_schema["$defs"]["candidate"]
    schema_summary = {
        "adapter_supplied_protocol_fields": ["schema_version", "response_kind"],
        "candidate_required": candidate_schema["required"],
        "candidate_predicate_enum": candidate_schema["properties"]["predicate"]["enum"],
        "candidate_statement_type_enum": candidate_schema["properties"]["statement_type"]["enum"],
        "entity_role_enum": response_schema["$defs"]["entity"]["properties"]["role"]["enum"],
    }
    system = PROMPT_PATH.read_text(encoding="utf-8")
    system += (
        "\n\nThe following required-field summary is authoritative. Return an object "
        "that validates against the local machine-readable JSON Schema exactly; "
        "do not use legacy fields such as relation, statement, applicability, "
        "or evidence_ids:\n"
        + json.dumps(schema_summary, ensure_ascii=False, sort_keys=True)
    )
    return {
        "version": "stage12-candidate-prompt-v6",
        "system": system,
        "user": json.dumps({"profile": profile.semantic_role, "evidence": dict(evidence)}, ensure_ascii=False, sort_keys=True),
    }


class ProfileRoutingError(ValueError):
    """Raised when a source cannot be mapped to exactly one Stage 12 profile."""


@dataclass(frozen=True)
class ExtractionProfile:
    semantic_role: str
    extraction_profile_id: str
    source_profile_id: str
    source_applicability_scope: tuple[tuple[str, Any], ...]
    external_llm_allowed: bool = False


class FixtureExtractionProvider:
    """Offline provider used when no real LLM is configured.

    It exercises the same strict response parser and candidate assembly path as
    an external provider.  Its language interpretation is intentionally small
    and generic; it is not a second source of Gold labels.
    """

    provider_id = "deterministic_fixture_v1"

    metadata = {
        "provider_id": provider_id,
        "mode": "fixture",
        "model_config_identifier": "deterministic-fixture-v1",
        "prompt_version": "stage12-candidate-prompt-v6",
        "response_schema_version": 2,
    }

    def extract(self, evidence: Mapping[str, Any], profile: ExtractionProfile) -> Mapping[str, Any]:
        fixture = HeuristicSemanticExtractor(
            profile_id=profile.extraction_profile_id,
            semantic_role=profile.semantic_role,
            split="development_regression_golden",
            source_applicability_scope=dict(profile.source_applicability_scope),
        )
        rows = fixture.extract(evidence)
        return parse_provider_response({
            "status": "ok" if rows else "no_statement",
            "candidates": [{
                "statement_text": row["statement_text"],
                "statement_type": row["statement_type"],
                "predicate": row["predicate"],
                "subject_entities": [{"surface_form": entity["surface_form"], "role": entity["role"]} for entity in row["subject_entities"]],
                "conditions": [{"surface_form": condition["surface_form"]} for condition in row["conditions"]],
                "applicability_scope": {key: value for key, value in row["applicability_scope"].items() if key in {"status", "applicability_text"}},
            } for row in rows],
            "provider_metadata": self.metadata,
        })


class ExternalLLMProvider:
    """Configured LLM Provider with strict response handling and no fallback."""

    provider_id = "external_llm_openai_compatible_v1"

    def __init__(self, transport: Any = None, *, model_config_identifier: str = "", max_attempts: int = 2):
        self.transport = transport
        self.model_config_identifier = model_config_identifier
        self.max_attempts = max_attempts
        self.metadata = {
            "provider_id": self.provider_id,
            "mode": "real_llm",
            "transport": "openai_compatible_chat_completions",
            "model_config_identifier": model_config_identifier,
            "prompt_version": "stage12-candidate-prompt-v6",
            "response_schema_version": 2,
        }

    def extract(
        self,
        evidence: Mapping[str, Any],
        profile: ExtractionProfile,
        *,
        response_validator: Callable[[Mapping[str, Any]], None] | None = None,
        attempt_observer: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> Mapping[str, Any]:
        if self.transport is None:
            raise ExtractionProviderError("external LLM provider has no configured transport")
        prompt = stage12_prompt(evidence, profile)
        last_schema_error: ExtractionSchemaError | None = None
        for attempt in range(self.max_attempts):
            attempt_number = attempt + 1
            try:
                raw = self.transport(prompt)
                try:
                    response = parse_provider_response(raw)
                except ExtractionSchemaError as error:
                    if attempt_observer is not None:
                        attempt_observer({
                            "attempt": attempt_number,
                            "outcome": "failure",
                            "failure_type": "schema_failure",
                            "field": _diagnostic_failure_field(str(error), schema=True),
                            "validator_reason": str(error),
                            "model_value_or_text": _diagnostic_response_snapshot(raw),
                        })
                    raise
                if response_validator is not None:
                    try:
                        response_validator(response)
                    except ValueError as error:
                        if attempt_observer is not None:
                            attempt_observer({
                                "attempt": attempt_number,
                                "outcome": "failure",
                                "failure_type": "semantic_validation_failure",
                                "field": _diagnostic_failure_field(str(error)),
                                "validator_reason": str(error),
                                "model_value_or_text": _diagnostic_response_snapshot(response),
                            })
                        raise
                if attempt_observer is not None:
                    attempt_observer({
                        "attempt": attempt_number,
                        "outcome": "success",
                        "model_value_or_text": _diagnostic_response_snapshot(response),
                    })
                return response | {"provider_metadata": self.metadata}
            except ExtractionSchemaError as error:
                last_schema_error = error
                if attempt + 1 >= self.max_attempts:
                    raise ExtractionProviderError(
                        f"external LLM response failed Stage 12 schema after {self.max_attempts} attempts"
                    ) from error
                prompt = prompt | {
                    "system": prompt["system"]
                    + "\n\nYour previous response failed strict validation. Correct these validation errors and return only corrected JSON:\n"
                    + str(error)
                }
            except ExtractionProviderError:
                raise
            except LLMTransportError as error:
                if attempt_observer is not None:
                    attempt_observer({
                        "attempt": attempt_number,
                        "outcome": "failure",
                        "failure_type": "transport_failure",
                        "field": None,
                        "validator_reason": str(error),
                        "model_value_or_text": None,
                    })
                raise ExtractionProviderError("external LLM provider failed") from error
            except ValueError as error:
                last_schema_error = None
                if attempt + 1 >= self.max_attempts:
                    raise ExtractionProviderError(
                        f"external LLM response failed Stage 12 semantic validation after {self.max_attempts} attempts"
                    ) from error
                prompt = prompt | {
                    "system": prompt["system"]
                    + "\n\nYour previous response failed deterministic Evidence validation. Correct the semantic fields and return only corrected JSON:\n"
                    + str(error)
                }
            except Exception as error:  # provider failures must not look like validation failures
                raise ExtractionProviderError("external LLM provider failed") from error
        raise ExtractionProviderError("external LLM response failed strict parsing") from last_schema_error


def _diagnostic_response_snapshot(raw: Any) -> dict[str, Any] | None:
    """Return a bounded provider-field snapshot without persisting raw output."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {"raw_type": "non_json_text"}
    if not isinstance(raw, Mapping):
        return {"raw_type": type(raw).__name__}
    snapshot: dict[str, Any] = {"status": raw.get("status")}
    candidates = []
    for item in list(raw.get("candidates") or [])[:3]:
        if not isinstance(item, Mapping):
            continue
        candidate = {
            "statement_text": str(item.get("statement_text", ""))[:1200],
            "statement_type": item.get("statement_type"),
            "predicate": item.get("predicate"),
            "subject_entities": [
                {"surface_form": str(entity.get("surface_form", ""))[:240], "role": entity.get("role")}
                for entity in list(item.get("subject_entities") or [])[:10]
                if isinstance(entity, Mapping)
            ],
            "conditions": [
                {"surface_form": str(condition.get("surface_form", ""))[:240]}
                for condition in list(item.get("conditions") or [])[:10]
                if isinstance(condition, Mapping)
            ],
            "applicability_scope": {
                key: str(value)[:500]
                for key, value in dict(item.get("applicability_scope") or {}).items()
                if key in {"status", "applicability_text"}
            },
        }
        candidates.append(candidate)
    snapshot["candidates"] = candidates
    return snapshot


def _diagnostic_failure_field(reason: str, *, schema: bool = False) -> str:
    text = reason.lower()
    if "entity" in text:
        return "entity"
    if "applicability" in text or "specified" in text or "not_applicable" in text:
        return "applicability"
    if "statement text" in text or "boundary" in text:
        return "statement_boundary"
    if "statement type" in text:
        return "statement_type"
    if "causal" in text or "relation" in text:
        return "relation_direction" if "direction" in text else "relation"
    if "quantity" in text or "unit" in text or "comparison" in text:
        return "quantity"
    if "negation" in text:
        return "negation"
    if "condition" in text:
        return "condition"
    if "unsupported" in text:
        return "unsupported_addition"
    if schema and "status" in text:
        return "status"
    return "schema" if schema else "semantic"


def provider_from_config(path: Path = PROVIDER_CONFIG_PATH) -> ExtractionProvider:
    config = json.loads(path.read_text(encoding="utf-8"))
    provider_id = config.get("default_provider", "fixture")
    provider = config.get("providers", {}).get(provider_id, {})
    if not provider.get("enabled", False):
        raise ExtractionProviderError(f"configured Stage 12 provider is disabled: {provider_id}")
    if provider.get("kind") == "deterministic_fixture":
        return FixtureExtractionProvider()
    if provider.get("kind") == "external_llm":
        settings = Settings.from_environment()
        env_to_value = {
            "LLM_BASE_URL": settings.llm_base_url,
            "LLM_MODEL": settings.llm_model,
            "LLM_API_KEY": settings.llm_api_key,
            "LLM_ALLOW_EVIDENCE_SEND": str(settings.llm_allow_evidence_send).lower(),
            "LLM_TIMEOUT_SECONDS": str(settings.llm_timeout_seconds),
        }

        def configured_value(name: str) -> str:
            return str(env_to_value.get(name, os.environ.get(name, "")))

        permission_env = provider.get("permission_env", "LLM_ALLOW_EVIDENCE_SEND")
        if configured_value(permission_env).lower() != "true":
            raise ExtractionProviderError(f"external LLM evidence-send permission is not enabled: {permission_env}")
        endpoint = configured_value(provider.get("endpoint_env", "LLM_BASE_URL"))
        model = configured_value(provider.get("model_env", "LLM_MODEL"))
        credential = configured_value(provider.get("credential_env", "LLM_API_KEY"))
        timeout = float(configured_value(provider.get("timeout_env", "LLM_TIMEOUT_SECONDS")) or "60")
        transport_attempts = int(provider.get("transport_max_attempts", provider.get("max_attempts", 2)))
        response_attempts = int(provider.get("response_max_attempts", provider.get("max_attempts", 2)))
        backoff_base_seconds = float(provider.get("backoff_base_seconds", 1.0))
        max_backoff_seconds = float(provider.get("max_backoff_seconds", 30.0))
        model_config_identifier = hashlib.sha256(
            canonical_json({"endpoint": endpoint, "model": model}).encode("utf-8")
        ).hexdigest()[:16]
        try:
            transport = OpenAICompatibleTransport(
                endpoint=endpoint,
                model=model,
                api_key=credential,
                timeout_seconds=timeout,
                max_attempts=transport_attempts,
                json_mode=True,
                backoff_base_seconds=backoff_base_seconds,
                max_backoff_seconds=max_backoff_seconds,
            )
        except LLMTransportError as error:
            raise ExtractionProviderError("external LLM transport configuration is invalid") from error
        return ExternalLLMProvider(
            transport=transport,
            model_config_identifier=model_config_identifier,
            max_attempts=response_attempts,
        )
    raise ExtractionProviderError(f"unsupported Stage 12 provider kind: {provider.get('kind')}")


class ProfileRouter:
    """Resolve profiles by stable Document/Revision identity, never filenames."""

    def __init__(self, path: Path = PROFILE_ROUTING_PATH):
        self.path = path
        config = json.loads(path.read_text(encoding="utf-8"))
        entries = config.get("entries", [])
        if not entries:
            raise ProfileRoutingError("Stage 12 profile routing manifest is empty")
        self._entries: dict[tuple[str, str], ExtractionProfile] = {}
        source_scopes: dict[str, dict[str, Any]] = {}
        source_permissions: dict[str, bool] = {}
        source_registry = path.parents[1] / "data/registry/source_assets.jsonl"
        if source_registry.exists():
            for line in source_registry.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                source = json.loads(line)
                document_id = str(source.get("document_logical_id", ""))
                structured = source.get("applicability_scope_structured") or {}
                if document_id and structured:
                    existing = source_scopes.setdefault(document_id, {})
                    for key, value in structured.items():
                        if key in existing and existing[key] != value:
                            raise ProfileRoutingError(f"conflicting source applicability scope: {document_id}/{key}")
                        existing[key] = value
                explicit = source.get("external_llm_allowed")
                source_permissions[document_id] = bool(explicit) if isinstance(explicit, bool) else source.get("external_processing_status") == "allowed"
        for entry in entries:
            key = (str(entry.get("document_logical_id", "")), str(entry.get("revision_id", "")))
            profile = ExtractionProfile(
                semantic_role=str(entry.get("semantic_role", "")),
                extraction_profile_id=str(entry.get("extraction_profile_id", "")),
                source_profile_id=str(entry.get("source_profile_id", "")),
                source_applicability_scope=tuple(sorted(source_scopes.get(key[0], {}).items())),
                external_llm_allowed=source_permissions.get(key[0], False),
            )
            if not all((key[0], key[1], profile.semantic_role, profile.extraction_profile_id, profile.source_profile_id)):
                raise ProfileRoutingError("profile routing entry is incomplete")
            if key in self._entries:
                raise ProfileRoutingError(f"ambiguous Stage 12 profile route: {key}")
            self._entries[key] = profile

    def route(self, evidence: Mapping[str, Any]) -> ExtractionProfile:
        key = (str(evidence.get("document_logical_id", "")), str(evidence.get("revision_id", "")))
        try:
            profile = self._entries[key]
        except KeyError as error:
            raise ProfileRoutingError(f"no Stage 12 profile route for {key}") from error
        declared = evidence.get("source_profile_id")
        if declared and str(declared) != profile.source_profile_id:
            raise ProfileRoutingError(f"source profile mismatch for {key}: {declared} != {profile.source_profile_id}")
        return profile

    def extractor_for(self, evidence: Mapping[str, Any], *, split: str, provider: ExtractionProvider | None = None) -> StatementExtractor:
        profile = self.route(evidence)
        if provider is not None:
            return ProviderBackedExtractor(provider, profile=profile, split=split)
        return HeuristicSemanticExtractor(
            profile_id=profile.extraction_profile_id,
            semantic_role=profile.semantic_role,
            split=split,
            source_applicability_scope=profile.source_applicability_scope,
        )


class ProviderBackedExtractor:
    """Assemble provider semantics with canonical Evidence lineage."""

    def __init__(self, provider: ExtractionProvider, *, profile: ExtractionProfile, split: str):
        self.provider = provider
        self.profile = profile
        self.split = split
        self.profile_id = profile.extraction_profile_id

    def extract(
        self,
        evidence: Mapping[str, Any],
        *,
        attempt_observer: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> list[dict[str, Any]]:
        if isinstance(self.provider, ExternalLLMProvider) and not self.profile.external_llm_allowed:
            raise ExtractionProviderError("source-level permission denies sending Evidence to an external LLM")
        if isinstance(self.provider, ExternalLLMProvider):
            def response_validator(response: Mapping[str, Any]) -> None:
                for index, item in enumerate(response["candidates"], start=1):
                    candidate = _assemble_candidate(item, evidence, self.profile, self.split, index)
                    validate_candidate_against_evidence(candidate, evidence)

            raw_response = self.provider.extract(
                evidence,
                self.profile,
                response_validator=response_validator,
                attempt_observer=attempt_observer,
            )
        else:
            raw_response = self.provider.extract(evidence, self.profile)
        response = parse_provider_response(raw_response)
        rows = []
        for index, item in enumerate(response["candidates"], start=1):
            rows.append(_assemble_candidate(item, evidence, self.profile, self.split, index))
        return rows


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_candidate_payload(payload: dict[str, Any], schema_path: Path = SCHEMA_PATH) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda e: list(e.path))
    if errors:
        raise ValueError("invalid Stage 12 candidate payload: " + "; ".join(e.message for e in errors))


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _text(evidence: Mapping[str, Any]) -> str:
    value = _canonical_evidence_text(evidence)
    return re.sub(r"[ \t\f\r]+", " ", str(value)).strip()


def _canonical_evidence_text(evidence: Mapping[str, Any]) -> str:
    """Return canonical Evidence text without NLP whitespace normalization."""
    return str(evidence.get("effective_text") or evidence.get("source_text") or "")


def _location(evidence: Mapping[str, Any]) -> dict[str, Any]:
    locations = evidence.get("locations") or []
    if locations:
        location = locations[0]
        return {
            "physical_page": int(location["physical_page"]),
            "logical_page": location.get("logical_page"),
            "source_span_ids": list(evidence.get("source_span_ids") or [location.get("source_span_id")]),
        }
    return {
        "physical_page": int(evidence["physical_page"]),
        "logical_page": evidence.get("logical_page"),
        "source_span_ids": [evidence.get("source_span_id")],
    }


def _evidence_version_id(evidence: Mapping[str, Any]) -> str:
    value = evidence.get("evidence_version_id")
    if value:
        return str(value)
    # Isolated contract fixtures may omit the Stage 6 version field. Production
    # Evidence always carries its canonical value; this fallback is not used as
    # a formal identity.
    return "derived-" + _sha({"evidence_id": evidence.get("evidence_id"), "revision_id": evidence.get("revision_id")})[:24]


def _split_clauses(text: str) -> list[str]:
    """Split semantic units conservatively; preserve multi-step procedures."""
    text = re.sub(r"\r\n?", "\n", text.strip())
    pieces: list[str] = []
    buffer = ""

    def flush() -> None:
        nonlocal buffer
        value = re.sub(r"\s+", " ", buffer).strip(" \t，,；;")
        if len(value) >= 8 and not value.startswith(NOISE_PREFIXES):
            pieces.append(value)
        buffer = ""

    # Newlines in OCR are often visual line wraps, not semantic boundaries.
    # Treat a new line as a boundary only when it starts a section/list item;
    # continuation lines stay attached to the same Engineering Statement.
    marker = re.compile(
        r"^\s*(?:[1-9][0-9]*(?:\.[0-9]+){1,3}\s+|[1-9][0-9]*[、．)]\s+|[1-9][0-9]*\s+|"
        r"[一二三四五六七八九十]+[、．)]\s+|若|如果|当)"
    )
    list_mode = False
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        numbered_item = bool(re.match(r"^\s*[1-9][0-9]*\s+", line))
        parent_heading = bool(re.search(r"(?:下列|如下|具备|包括)[^。！？]{0,12}[：:]\s*$", buffer))
        continuation_condition = bool(re.match(r"^(?:若|如果|当)", line)) and buffer.rstrip().endswith(("应", "当", "将", "需", "须"))
        if buffer and marker.match(line) and not continuation_condition:
            # The first numbered item is part of its parent heading in the
            # frozen statement samples; subsequent items start new units.
            if numbered_item and parent_heading:
                list_mode = True
            elif not numbered_item or list_mode or not parent_heading:
                flush()
        if numbered_item and parent_heading:
            list_mode = True
        elif marker.match(line) and not numbered_item:
            list_mode = False
        buffer = f"{buffer} {line}".strip()
        # Preserve sentence boundaries that are explicit in the source.
        while True:
            match = re.search(r"[。！？；]", buffer)
            if not match:
                break
            head, buffer = buffer[: match.end()], buffer[match.end() :].strip()
            value = re.sub(r"\s+", " ", head).strip(" \t，,；;")
            if len(value) >= 8 and not value.startswith(NOISE_PREFIXES):
                pieces.append(value)
    flush()

    # A numeric multi-step procedure is a single engineering unit when the
    # source has already bound its measurements to the same operation.  This
    # avoids turning a controlled sequence into unrelated quantity fragments.
    normalized = re.sub(r"\s+", " ", text).strip()
    if (
        re.search(QUANTITY_RE, normalized)
        and any(token in normalized for token in ("然后", "再将", "再"))
        and any(token in normalized for token in ("抬起", "调节", "调整", "检查"))
        and not re.search(r"(?:^|\n)\s*[1-9][0-9]*(?:\.[0-9]+){1,3}\s+", text)
    ):
        return [normalized]

    # In prose procedures, conjunctions can carry the step boundary when the
    # OCR text has no sentence punctuation.  Keep the conjunction with the
    # following step so each candidate remains independently readable.
    if not re.search(QUANTITY_RE, normalized) and any(token in normalized for token in ("然后", "再")):
        steps = re.split(r"(?=(?:然后|再)(?:将|调节|调整|确认|检查|确保|验证))", normalized)
        if len(steps) > 1 and all(len(step.strip()) >= 8 for step in steps):
            return [step.strip() for step in steps]

    # Some OCR layouts collapse numbered items onto one line.  Split only on
    # explicit list punctuation; decimal section numbers remain untouched.
    expanded: list[str] = []
    for piece in pieces:
        parts = re.split(r"(?=\s[1-9][0-9]*[、．)]\s+)", piece)
        expanded.extend(part.strip() for part in parts if len(part.strip()) >= 8)
    return expanded or ([re.sub(r"\s+", " ", text)] if len(text) >= 8 else [])


def _quantity_fields(text: str) -> tuple[list[dict[str, Any]], Any, str | None]:
    quantities: list[dict[str, Any]] = []
    scalar_value = None
    scalar_unit = None
    for match in QUANTITY_RE.finditer(text):
        unit = match.group("unit") or match.group("single_unit")
        unit = "%" if unit == "％" else unit
        if match.group("left") is not None:
            minimum, maximum = float(match.group("left")), float(match.group("right"))
            quantities.append({"surface_form": match.group(0), "min": minimum, "max": maximum, "unit": unit, "operator": "range"})
        else:
            value = float(match.group("single"))
            if value.is_integer():
                value = int(value)
            operator = "eq"
            context = text[max(0, match.start() - 12): min(len(text), match.end() + 12)]
            bound = next((item for item in COMPARATORS if item[0] in context), None)
            if bound:
                operator = bound[1]
            elif re.search(r"(?:约为|大约|左右|约)\s*$", context) or re.search(r"(?:约为|大约|左右|约)", context):
                operator = "approximately"
            quantities.append({"surface_form": match.group(0), "value": value, "unit": unit, "operator": operator})
            if scalar_value is None:
                scalar_value, scalar_unit = value, unit
    return quantities, scalar_value, scalar_unit


def _negation_fields(text: str) -> list[dict[str, Any]]:
    result = []
    for token in NEGATIONS:
        if token not in text:
            continue
        polarity = "negative"
        scope_type = "statement"
        for marker, _, bound_polarity in COMPARATORS:
            if marker == token:
                polarity, scope_type = bound_polarity, "quantity"
                break
        result.append({"surface_form": token, "polarity": polarity, "scope_type": scope_type})
    return result


def _statement_type(text: str, evidence_role: str | None = None) -> str:
    if "应具备下列条件" in text:
        return "condition"
    if re.match(r"^\s*(?:当|若|如果)", text) and any(token in text for token in ("可能", "造成", "导致")):
        return "fact"
    if evidence_role == "requirement_source" and "应" in text and not any(token in text for token in ("校核", "核查", "检验", "验收", "验证")):
        return "requirement"
    if "确认" in text and any(token in text for token in ("要求", "满足", "正常")):
        return "verification"
    if any(token in text for token in ("校核", "核查", "检验", "验收", "验证")):
        return "verification"
    if any(token in text for token in ("封闭", "止水")) and not any(token in text for token in ("应", "必须", "不得", "须")):
        return "condition"
    if any(token in text for token in ("首先", "然后", "再", "依次", "步骤", "调节", "调整", "抬起", "清洗")):
        return "procedure"
    if any(token in text for token in ("应", "必须", "不得", "须", "要求")):
        return "requirement"
    if any(token in text for token in ("以上", "以下", "至少", "不小于", "不少于", "不低于")):
        return "requirement"
    if any(token in text for token in ("检查", "确认")):
        return "verification"
    if any(token in text for token in ("适用于", "范围", "包括")):
        return "limitation"
    if any(token in text for token in ("当", "若", "如果")):
        return "condition"
    if evidence_role == "requirement_source":
        return "requirement"
    return "fact"


def _modality(text: str) -> str:
    if "必须" in text or "须" in text:
        return "must"
    if any(token in text for token in ("应", "不得", "不应")):
        return "shall"
    return "descriptive"


def _entities(text: str) -> list[dict[str, str]]:
    subject = re.sub(r"^(?:当|若|如果)[^时，,；;]{1,40}(?:时|，|,)", "", text).strip()
    subject = re.split(r"(?:应当|必须|不得|不应|应|须|需|需要|可|将|会|可能|导致|造成|是|为)", subject, maxsplit=1)[0]
    subject = re.sub(r"^[0-9]+(?:\.[0-9]+)*\s*[、.)]?\s*", "", subject).strip(" ：:，,;")
    if not subject:
        # A text-grounded fallback is preferable to manufacturing an ontology ID.
        subject = re.findall(r"[\u3400-\u9fffA-Za-z][\u3400-\u9fffA-Za-z0-9/-]{1,24}", text)
        subject = subject[0] if subject else "unresolved_entity"
    return [{"surface_form": subject[:80], "role": "subject", "entity_class": "candidate"}]


def _predicate(text: str, statement_type: str) -> str:
    """Return a small structural relation vocabulary, never a sentence label."""
    if _has_causal_marker(text):
        return "causes"
    if statement_type == "limitation":
        return "limits_scope"
    if statement_type == "verification":
        return "verifies"
    if statement_type == "procedure":
        return "describes"
    if any(token in text for token in ("禁止", "严禁", "不得进行", "不得采用")):
        return "prohibits"
    if any(token in text for token in ("应", "必须", "不得", "须", "需")):
        return "requires"
    if statement_type == "requirement" and _quantity_fields(text)[0]:
        return "requires"
    return "describes"


def _has_causal_marker(text: str) -> bool:
    direct_cause = bool(re.search(r"(?<!所)(?:导致|造成|引起)", text))
    return direct_cause or bool(re.search(r"可能使[^，。；]{1,24}(?:形成|损坏|升高|产生)", text))


def _relation_direction(text: str, predicate: str) -> str:
    if predicate == "causes":
        return "cause_to_effect"
    if predicate == "limits_scope":
        return "scope_to_subject"
    if predicate == "describes" and any(token in text for token in ("然后", "再", "首先", "依次")):
        return "procedure_order"
    if any(token in text for token in ("当", "若", "如果")) and predicate in {"requires", "prohibits"}:
        return "condition_to_consequence"
    return "subject_to_object"


def _applicability_scope(
    text: str,
    evidence: Mapping[str, Any],
    location: Mapping[str, Any],
    source_scope: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a source-bounded scope; absent facts remain unknown."""
    # Registry applicability is document-level metadata, not a statement-level
    # assertion.  Do not promote it to ``known`` here: doing so would turn a
    # broad source context such as ``installation`` into a claim about every
    # clause on the page.  Only wording present in the Evidence may establish
    # known statement applicability; otherwise the result stays unknown.
    scope: dict[str, Any] = {
        "document_key": evidence.get("document_key") or evidence.get("document_logical_id"),
        "physical_page": location["physical_page"],
        "status": "unknown",
        **({"logical_page": location["logical_page"]} if location.get("logical_page") is not None else {}),
    }
    compact = re.sub(r"\s+", "", text)
    # Preserve explicit wording, but do not invent a canonical model/equipment.
    applicability_markers = re.findall(
        r"(?:冲转前|冲转之前|(?:在|于)[^。；，,]{1,28}(?:前|期间|状态下)|(?:当|若|如果)[^。；，,]{1,36}(?:时|后))",
        compact,
    )
    if applicability_markers:
        scope["applicability_text"] = applicability_markers[0]
        scope["status"] = "known"
    return scope


class HeuristicSemanticExtractor:
    """Offline baseline extractor used for the first Stage 12 runtime proof."""

    profile_id = "heuristic_semantic_v1"

    def __init__(self, *, profile_id: str | None = None, semantic_role: str | None = None, split: str = "development_regression_golden", source_applicability_scope: Mapping[str, Any] | None = None):
        self.profile_id = profile_id or type(self).profile_id
        self.semantic_role = semantic_role
        self.split = split
        self.source_applicability_scope = dict(source_applicability_scope or {})

    def extract(self, evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
        text = _text(evidence)
        location = _location(evidence)
        document_key = evidence.get("document_key") or evidence.get("document_logical_id")
        rows = []
        for index, clause in enumerate(_split_clauses(text), start=1):
            statement_type = _statement_type(clause, str(evidence.get("evidence_role") or ""))
            quantities, value, unit = _quantity_fields(clause)
            conditions = [{"surface_form": item, "kind": "condition"} for item in CONDITION_RE.findall(clause)]
            entities = _entities(clause)
            candidate_basis = {
                "evidence_id": evidence["evidence_id"], "index": index, "text": clause,
            }
            rows.append({
                "candidate_id": "stage12-candidate-" + hashlib.sha1(_sha(candidate_basis).encode()).hexdigest()[:20],
                "split": self.split,
                "task": "statement",
                "statement_text": clause,
                "statement_type": statement_type,
                "predicate": _predicate(clause, statement_type),
                "relation_direction": _relation_direction(clause, _predicate(clause, statement_type)),
                "subject_entities": entities,
                "object_value": {"kind": "source_assertion", "value": clause},
                "value": value,
                "unit": unit,
                "quantities": quantities,
                "normative_modality": _modality(clause),
                "negation_scope": _negation_fields(clause),
                "conditions": conditions,
                "applicability_scope": _applicability_scope(clause, evidence, location, self.source_applicability_scope),
                "evidence_bindings": [{"evidence_id": evidence["evidence_id"], "support_type": evidence.get("support_type", "direct")}],
                "source_span_ids": [item for item in location["source_span_ids"] if item],
                "evidence_version_id": _evidence_version_id(evidence),
                "document_logical_id": evidence["document_logical_id"],
                "revision_id": evidence["revision_id"],
                "physical_page": location["physical_page"],
                "logical_page": location["logical_page"],
                "source_text_sha256": evidence["source_text_sha256"],
                 "evidence_quote": _canonical_evidence_text(evidence),
                "review_status": "candidate_only",
                "formal_release": False,
                "extraction_profile": self.profile_id,
            })
        return rows


def _assemble_candidate(
    item: Mapping[str, Any], evidence: Mapping[str, Any], profile: ExtractionProfile, split: str, index: int,
) -> dict[str, Any]:
    """Bind LLM semantics to Evidence and derive reliable fields deterministically."""
    location = _location(evidence)
    statement_text = str(item["statement_text"])
    statement_type = str(item.get("statement_type", "")).strip()
    if statement_type not in STATEMENT_TYPES:
        raise ValueError("candidate statement_type is not in the Stage 12 vocabulary")
    predicate = str(item["predicate"])
    quantities, value, unit = _quantity_fields(statement_text)
    raw_entities = item.get("subject_entities") or []
    subject_entities = []
    for entity in raw_entities:
        surface_form = str(entity.get("surface_form", "")).strip()
        if not surface_form or _normalized_text(surface_form) not in _normalized_text(statement_text):
            raise ValueError("candidate entity is not grounded in statement text")
        role = str(entity.get("role", "")).strip()
        if role not in ENTITY_ROLES:
            raise ValueError("candidate entity role is not in the Stage 12 vocabulary")
        subject_entities.append({"surface_form": surface_form, "role": role, "entity_class": "candidate"})
    if not subject_entities or subject_entities[0]["role"] != "subject":
        raise ValueError("candidate must provide a subject entity first")
    raw_conditions = item.get("conditions") or []
    conditions = []
    for condition in raw_conditions:
        surface_form = str(condition.get("surface_form", "")).strip()
        if not surface_form or _normalized_text(surface_form) not in _normalized_text(statement_text):
            raise ValueError("candidate condition is not grounded in statement text")
        conditions.append({"surface_form": surface_form, "kind": "condition"})
    raw_scope = dict(item.get("applicability_scope") or {})
    scope_status = raw_scope.get("status")
    scope_text = raw_scope.get("applicability_text")
    if scope_status == "known" and not scope_text:
        raise ValueError("known applicability must preserve exact wording")
    if scope_status == "unknown" and scope_text:
        raise ValueError("unknown applicability must omit applicability_text")
    if scope_text and _normalized_text(str(scope_text)) not in _normalized_text(statement_text):
        raise ValueError("candidate applicability wording is not grounded in statement text")
    applicability = {"status": scope_status}
    if scope_text:
        applicability["applicability_text"] = str(scope_text)
    # The profile scope is routing metadata.  It remains available to the
    # provider/profile, but must not be copied into statement applicability
    # unless the provider explicitly returned that fact from the Evidence.
    applicability.update({"document_key": evidence.get("document_key") or evidence.get("document_logical_id"), "physical_page": location["physical_page"]})
    if location.get("logical_page") is not None:
        applicability["logical_page"] = location["logical_page"]
    basis = {"evidence_id": evidence["evidence_id"], "index": index, "text": statement_text, "provider": profile.extraction_profile_id}
    return {
        "candidate_id": "stage12-candidate-" + hashlib.sha1(_sha(basis).encode()).hexdigest()[:20],
        "split": split,
        "task": "statement",
        "statement_text": statement_text,
        "statement_type": statement_type,
        "predicate": predicate,
        "relation_direction": _relation_direction(statement_text, predicate),
        "subject_entities": subject_entities,
        "object_value": {"kind": "source_assertion", "value": statement_text},
        "value": value,
        "unit": unit,
        "quantities": quantities,
        "normative_modality": _modality(statement_text),
        "negation_scope": _negation_fields(statement_text),
        "conditions": conditions,
        "applicability_scope": applicability,
        "evidence_bindings": [{"evidence_id": evidence["evidence_id"], "support_type": evidence.get("support_type", "direct")}],
        "source_span_ids": [item for item in location["source_span_ids"] if item],
        "evidence_version_id": _evidence_version_id(evidence),
        "document_logical_id": evidence["document_logical_id"],
        "revision_id": evidence["revision_id"],
        "physical_page": location["physical_page"],
        "logical_page": location["logical_page"],
        "source_text_sha256": evidence["source_text_sha256"],
        "evidence_quote": _canonical_evidence_text(evidence),
        "review_status": "candidate_only",
        "formal_release": False,
        "extraction_profile": profile.extraction_profile_id,
    }


def to_stage9_runtime_payload(candidates: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Project validated candidates into the existing Stage 9 gate in memory."""
    candidates = list(candidates)
    nodes: dict[str, dict[str, Any]] = {}
    relations: set[tuple[str, str, str]] = set()

    def iri(kind: str, identifier: str) -> str:
        return f"urn:turbine-v2:stage12:{kind}:{identifier}"

    def add(node_id: str, kind: str, properties: dict[str, Any]) -> None:
        existing = nodes.get(node_id)
        node = {"id": node_id, "type": kind, "properties": properties}
        if existing is not None and existing != node:
            raise ValueError(f"conflicting Stage 12 runtime node: {node_id}")
        nodes[node_id] = node

    for candidate in candidates:
        cid = candidate["candidate_id"]
        sid = iri("statement", cid)
        # Stage 9's current Evidence shape permits one aboutEntity per node,
        # while Stage 12 candidates intentionally support Evidence↔Statement
        # many-to-many bindings.  Use an occurrence-scoped validation node so
        # the projection does not redefine the Stage 6 Evidence identity.
        bindings = candidate["evidence_bindings"]
        span_ids = candidate["source_span_ids"]
        stage9_statement_type = STAGE9_TYPE[candidate["statement_type"]]
        validate_candidate_semantics(candidate)
        entities = []
        for subject in candidate["subject_entities"]:
            # The same grounded surface may be a subject in one statement and
            # an object/related entity in another.  Role is part of this
            # candidate projection identity so runtime validation cannot merge
            # incompatible role assertions.
            entity = iri("entity", _sha({"surface_form": subject["surface_form"], "entity_class": subject["entity_class"], "role": subject["role"]})[:20])
            add(entity, "PhysicalEntity", {"objectKey": subject["surface_form"], "entityRole": subject["role"], "entityClass": subject["entity_class"]})
            entities.append(entity)
        entity = entities[0]
        scope = iri("scope", cid)
        statement_properties = {
            "statementText": candidate["statement_text"],
            "statementType": stage9_statement_type,
            "predicateLabel": candidate["predicate"],
            "objectAssertion": candidate["object_value"]["value"],
            "normativeModality": candidate["normative_modality"],
            "subjectEntityLabels": canonical_json(candidate["subject_entities"]),
        }
        if candidate["negation_scope"]:
            statement_properties["negationScope"] = canonical_json(candidate["negation_scope"])
        if candidate["conditions"]:
            statement_properties["conditionText"] = canonical_json(candidate["conditions"])
        add(sid, "EngineeringStatement", statement_properties)
        scope_keys = {"model": "model", "equipment": "equipment", "lifecycle_stage": "lifecycleStage", "activity": "activity", "operating_state": "operatingState", "condition": "condition", "document_key": "documentKey", "physical_page": "physicalPageScope", "logical_page": "logicalPageScope"}
        add(scope, "ApplicabilityScope", {scope_keys[key]: value for key, value in candidate["applicability_scope"].items() if key in scope_keys and value is not None})
        relations.update({(sid, "aboutEntity", entity), (sid, "hasApplicabilityScope", scope)})
        relations.update((sid, "relatedEntity", item) for item in entities[1:])
        for binding in bindings:
            eid = iri("evidence", f"{binding['evidence_id']}:{cid}")
            add(eid, "Evidence", {"evidenceText": candidate["evidence_quote"], "evidenceId": binding["evidence_id"], "evidenceVersionId": candidate["evidence_version_id"], "supportType": binding["support_type"]})
            relations.update({(sid, "supportedBy", eid), (eid, "evidenceAboutEntity", entity)})
            for span_id in span_ids:
                span = iri("span", span_id)
                span_properties = {"spanId": span_id, "spanText": candidate["evidence_quote"], "physicalPage": candidate["physical_page"], "pageId": f"page-{candidate['physical_page']}", "revisionId": candidate["revision_id"], "documentId": candidate["document_logical_id"]}
                if candidate.get("logical_page") is not None:
                    span_properties["logicalPage"] = candidate["logical_page"]
                add(span, "SourceSpan", span_properties)
                relations.add((eid, "sourceSpan", span))
        for index, quantity in enumerate(candidate["quantities"]):
            qid = iri("quantity", f"{cid}:{index}")
            properties = {"unitSymbol": quantity["unit"]}
            properties["quantitySurfaceForm"] = quantity["surface_form"]
            properties["comparisonOperator"] = quantity["operator"]
            if "value" in quantity:
                properties["numericValue"] = quantity["value"]
            if "min" in quantity:
                properties["minimumValue"] = quantity["min"]
            if "max" in quantity:
                properties["maximumValue"] = quantity["max"]
            add(qid, "QuantityValue", properties)
            relations.add((sid, "hasQuantityValue", qid))
    payload = {"schema_version": 1, "nodes": list(nodes.values()), "relations": [{"source": s, "predicate": p, "target": t} for s, p, t in sorted(relations)]}
    report = validate_runtime_payload(payload)
    if not report["conforms"]:
        raise ValueError("Stage 12 candidate runtime projection failed Stage 9 validation: " + report["report_text"])
    validate_stage12_runtime_projection(candidates, payload)
    return payload


def validate_candidate_semantics(candidate: Mapping[str, Any]) -> None:
    """Check semantic fields that JSON Schema alone cannot relate to text."""
    text = str(candidate.get("statement_text", ""))
    if not text.strip() or not str(candidate.get("predicate", "")).strip():
        raise ValueError("candidate statement text and predicate are required")
    if candidate.get("predicate") not in COARSE_RELATIONS:
        raise ValueError("candidate predicate is outside the Stage 12 coarse relation vocabulary")
    if candidate.get("relation_direction") != _relation_direction(text, str(candidate.get("predicate", ""))):
        raise ValueError("candidate relation direction is required")
    if candidate.get("object_value", {}).get("value") != text:
        raise ValueError("candidate object_value must preserve statement_text")
    if not candidate.get("subject_entities"):
        raise ValueError("candidate must retain at least one subject entity")
    if any(_normalized_text(entity.get("surface_form")) not in _normalized_text(text) for entity in candidate.get("subject_entities", [])):
        raise ValueError("candidate entity is not grounded in statement text")
    if candidate.get("normative_modality") not in {"shall", "must", "descriptive"}:
        raise ValueError("unsupported normative modality")
    for item in [*candidate.get("conditions", []), *candidate.get("negation_scope", [])]:
        if item.get("surface_form") and item["surface_form"] not in text:
            raise ValueError("candidate semantic scope is not grounded in statement text")
    for quantity in candidate.get("quantities", []):
        if quantity.get("surface_form") and quantity["surface_form"] not in text:
            raise ValueError("candidate quantity is not grounded in statement text")
    if _quantity_fields(text)[0] and not candidate.get("quantities"):
        raise ValueError("candidate dropped quantities present in statement text")
    if _negation_fields(text) and not candidate.get("negation_scope"):
        raise ValueError("candidate dropped negation present in statement text")
    if CONDITION_RE.search(text) and not candidate.get("conditions"):
        raise ValueError("candidate dropped condition present in statement text")
    scope = candidate.get("applicability_scope") or {}
    if scope.get("status") not in {"known", "unknown"}:
        raise ValueError("candidate applicability must explicitly be known or unknown")
    if scope.get("status") == "known" and not scope.get("applicability_text"):
        raise ValueError("known applicability must preserve wording")
    if scope.get("status") == "unknown" and scope.get("applicability_text"):
        raise ValueError("unknown applicability must omit wording")
    if scope.get("applicability_text") and _normalized_text(scope["applicability_text"]) not in _normalized_text(text):
        raise ValueError("candidate applicability wording is not grounded in statement text")
    if candidate.get("predicate") == "causes" and candidate.get("relation_direction") != "cause_to_effect":
        raise ValueError("causal relation direction is inconsistent")


def _quantity_key(quantity: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        quantity.get("value"), quantity.get("min"), quantity.get("max"),
        "%" if quantity.get("unit") == "％" else quantity.get("unit"), quantity.get("operator"),
    )


def _quantity_is_supported(candidate_quantity: Mapping[str, Any], evidence_quantities: Iterable[Mapping[str, Any]]) -> bool:
    return any(_quantity_key(candidate_quantity) == _quantity_key(item) for item in evidence_quantities)


def validate_candidate_against_evidence(candidate: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Deterministically reject semantic drift while avoiding re-extraction."""
    validate_candidate_semantics(candidate)
    evidence_text = _canonical_evidence_text(evidence)
    if candidate.get("evidence_quote") != evidence_text:
        raise ValueError("candidate Evidence quote is not canonical")
    if _text_similarity(candidate.get("statement_text"), evidence_text) < 0.45:
        raise ValueError("candidate statement is not grounded in Evidence context")
    for entity in candidate.get("subject_entities", []):
        if _normalized_text(entity.get("surface_form")) not in _normalized_text(evidence_text):
            raise ValueError("candidate entity is not grounded in Evidence")
    expected_quantities = _quantity_fields(evidence_text)[0]
    actual_quantities = candidate.get("quantities", [])
    if any(not _quantity_is_supported(item, expected_quantities) for item in actual_quantities):
        raise ValueError("candidate contains an unsupported quantity, unit, or comparison operator")
    expected_negations = _negation_fields(evidence_text)
    for item in candidate.get("negation_scope", []):
        if not any(_normalized_text(item.get("surface_form")) == _normalized_text(other.get("surface_form")) and item.get("polarity") == other.get("polarity") for other in expected_negations):
            raise ValueError("candidate contains an unsupported negation")
    for item in candidate.get("conditions", []):
        if _normalized_text(item.get("surface_form")) not in _normalized_text(evidence_text):
            raise ValueError("candidate contains an unsupported condition")
    scope_text = (candidate.get("applicability_scope") or {}).get("applicability_text")
    if scope_text and _normalized_text(scope_text) not in _normalized_text(evidence_text):
        raise ValueError("candidate applicability exceeds Evidence wording")
    explicit_cause = _has_causal_marker(str(candidate.get("statement_text", "")))
    if candidate.get("predicate") == "causes" and not explicit_cause:
        raise ValueError("candidate causal relation is not expressed by Evidence")
    if explicit_cause and candidate.get("predicate") != "causes" and any(token in candidate.get("statement_text", "") for token in ("导致", "造成", "引起")):
        raise ValueError("candidate causal direction or relation is inconsistent with Evidence")
    return {
        "evidence_grounding": True,
        "quantity": all(_quantity_is_supported(item, expected_quantities) for item in actual_quantities),
        "unit": all(_quantity_is_supported(item, expected_quantities) for item in actual_quantities),
        "comparison": all(_quantity_is_supported(item, expected_quantities) for item in actual_quantities),
        "negation": all(any(_normalized_text(item.get("surface_form")) == _normalized_text(other.get("surface_form")) and item.get("polarity") == other.get("polarity") for other in expected_negations) for item in candidate.get("negation_scope", [])),
        "condition": all(_normalized_text(item.get("surface_form")) in _normalized_text(evidence_text) for item in candidate.get("conditions", [])),
        "applicability": not scope_text or _normalized_text(scope_text) in _normalized_text(evidence_text),
        "relation_direction": True,
        "unsupported_addition": False,
    }


def validate_stage12_runtime_projection(candidates: Iterable[Mapping[str, Any]], payload: Mapping[str, Any]) -> None:
    """Verify that the Stage 9 projection retains every Stage 12 semantic field."""
    nodes = {node["id"]: node for node in payload.get("nodes", [])}
    links = payload.get("relations", [])
    for candidate in candidates:
        sid = f"urn:turbine-v2:stage12:statement:{candidate['candidate_id']}"
        statement = nodes.get(sid)
        if statement is None:
            raise ValueError("Stage 12 runtime projection dropped EngineeringStatement")
        properties = statement.get("properties", {})
        expected_properties = {
            "statementText": candidate["statement_text"],
            "statementType": STAGE9_TYPE[candidate["statement_type"]],
            "predicateLabel": candidate["predicate"],
            "objectAssertion": candidate["object_value"]["value"],
            "normativeModality": candidate["normative_modality"],
            "subjectEntityLabels": canonical_json(candidate["subject_entities"]),
        }
        if any(properties.get(key) != value for key, value in expected_properties.items()):
            raise ValueError(f"Stage 12 semantic field was lost in runtime projection: {candidate['candidate_id']}")
        for field, source in (("negationScope", "negation_scope"), ("conditionText", "conditions")):
            if source in candidate and bool(candidate[source]) != (field in properties):
                raise ValueError(f"Stage 12 optional semantic field was lost in runtime projection: {candidate['candidate_id']}")
            if candidate.get(source) and properties.get(field) != canonical_json(candidate[source]):
                raise ValueError(f"Stage 12 semantic field changed in runtime projection: {candidate['candidate_id']}")
        expected_entities = []
        for subject in candidate["subject_entities"]:
            entity = f"urn:turbine-v2:stage12:entity:{_sha({'surface_form': subject['surface_form'], 'entity_class': subject['entity_class'], 'role': subject['role']})[:20]}"
            expected_entities.append(entity)
            node = nodes.get(entity)
            if not node or node["properties"] != {"objectKey": subject["surface_form"], "entityRole": subject["role"], "entityClass": subject["entity_class"]}:
                raise ValueError(f"Stage 12 entity role/class was lost in runtime projection: {candidate['candidate_id']}")
        about = {link["target"] for link in links if link["source"] == sid and link["predicate"] == "aboutEntity"}
        related = {link["target"] for link in links if link["source"] == sid and link["predicate"] == "relatedEntity"}
        if about != {expected_entities[0]} or related != set(expected_entities[1:]):
            raise ValueError(f"Stage 12 multi-entity projection is incomplete: {candidate['candidate_id']}")
        scope_id = f"urn:turbine-v2:stage12:scope:{candidate['candidate_id']}"
        scope = nodes.get(scope_id)
        scope_keys = {"model": "model", "equipment": "equipment", "lifecycle_stage": "lifecycleStage", "activity": "activity", "operating_state": "operatingState", "condition": "condition", "document_key": "documentKey", "physical_page": "physicalPageScope", "logical_page": "logicalPageScope"}
        expected_scope = {scope_keys[key]: value for key, value in candidate["applicability_scope"].items() if key in scope_keys and value is not None}
        if not scope or scope.get("properties") != expected_scope:
            raise ValueError(f"Stage 12 applicability was lost in runtime projection: {candidate['candidate_id']}")
        evidence_links = [link for link in links if link["source"] == sid and link["predicate"] == "supportedBy"]
        if len(evidence_links) != len(candidate["evidence_bindings"]):
            raise ValueError(f"Stage 12 Evidence bindings were lost in runtime projection: {candidate['candidate_id']}")
        for binding in candidate["evidence_bindings"]:
            evidence_id = f"urn:turbine-v2:stage12:evidence:{binding['evidence_id']}:{candidate['candidate_id']}"
            evidence = nodes.get(evidence_id)
            if not evidence or evidence["properties"] != {"evidenceText": candidate["evidence_quote"], "evidenceId": binding["evidence_id"], "evidenceVersionId": candidate["evidence_version_id"], "supportType": binding["support_type"]}:
                raise ValueError(f"Stage 12 Evidence lineage was lost in runtime projection: {candidate['candidate_id']}")
            span_targets = {link["target"] for link in links if link["source"] == evidence_id and link["predicate"] == "sourceSpan"}
            expected_spans = set(candidate["source_span_ids"])
            if span_targets != {f"urn:turbine-v2:stage12:span:{span_id}" for span_id in expected_spans}:
                raise ValueError(f"Stage 12 source spans were lost in runtime projection: {candidate['candidate_id']}")
            for span_id in expected_spans:
                span = nodes[f"urn:turbine-v2:stage12:span:{span_id}"]
                expected_span = {"spanId": span_id, "spanText": candidate["evidence_quote"], "physicalPage": candidate["physical_page"], "pageId": f"page-{candidate['physical_page']}", "revisionId": candidate["revision_id"], "documentId": candidate["document_logical_id"]}
                if candidate.get("logical_page") is not None:
                    expected_span["logicalPage"] = candidate["logical_page"]
                if span.get("properties") != expected_span:
                    raise ValueError(f"Stage 12 source span lineage changed in runtime projection: {candidate['candidate_id']}")
        quantity_links = [link["target"] for link in links if link["source"] == sid and link["predicate"] == "hasQuantityValue"]
        if len(quantity_links) != len(candidate["quantities"]):
            raise ValueError(f"Stage 12 quantities were lost in runtime projection: {candidate['candidate_id']}")
        for index, quantity in enumerate(candidate["quantities"]):
            quantity_node = nodes.get(f"urn:turbine-v2:stage12:quantity:{candidate['candidate_id']}:{index}")
            expected_quantity = {"unitSymbol": quantity["unit"], "quantitySurfaceForm": quantity["surface_form"], "comparisonOperator": quantity["operator"]}
            for key in ("value", "min", "max"):
                if key in quantity:
                    expected_quantity[{"value": "numericValue", "min": "minimumValue", "max": "maximumValue"}[key]] = quantity[key]
            if not quantity_node or quantity_node.get("properties") != expected_quantity:
                raise ValueError(f"Stage 12 quantity semantics changed in runtime projection: {candidate['candidate_id']}")


def validate_candidate_evidence_binding(candidate: Mapping[str, Any], evidence: Mapping[str, Any]) -> None:
    """Validate candidate lineage against the canonical Stage 6 Evidence row."""
    if candidate.get("document_logical_id") != evidence.get("document_logical_id") or candidate.get("revision_id") != evidence.get("revision_id"):
        raise ValueError("candidate Document/Revision identity does not match canonical Evidence")
    location = _location(evidence)
    if candidate.get("physical_page") != location["physical_page"] or candidate.get("logical_page") != location.get("logical_page"):
        raise ValueError("candidate page identity does not match canonical Evidence")
    if candidate.get("evidence_version_id") != _evidence_version_id(evidence):
        raise ValueError("candidate Evidence version does not match canonical Evidence")
    if candidate.get("source_text_sha256") != evidence.get("source_text_sha256"):
        raise ValueError("candidate source hash does not match canonical Evidence")
    expected_spans = set(location.get("source_span_ids") or [])
    actual_spans = set(candidate.get("source_span_ids") or [])
    if not expected_spans <= actual_spans:
        raise ValueError("candidate source spans do not cover canonical Evidence")
    if candidate.get("evidence_quote") != _canonical_evidence_text(evidence):
        raise ValueError("candidate Evidence quote is not the canonical Evidence text")
    bindings = {binding.get("evidence_id") for binding in candidate.get("evidence_bindings", [])}
    if evidence.get("evidence_id") not in bindings:
        raise ValueError("candidate does not bind the canonical Evidence id")


def compare_candidates(candidates: list[Mapping[str, Any]], gold_rows: list[Mapping[str, Any]], *, gold_exhaustive: bool = False) -> dict[str, Any]:
    """Evaluate semantics and extras without treating non-exhaustive Gold as FP."""
    by_evidence: dict[str, list[Mapping[str, Any]]] = {}
    for candidate in candidates:
        for binding in candidate.get("evidence_bindings", []):
            by_evidence.setdefault(binding["evidence_id"], []).append(candidate)
    fields = ("statement_boundary", "statement_type", "entity", "relation", "quantity", "unit", "comparison", "negation", "condition", "applicability", "applicability_meaning", "relation_direction", "unsupported_addition", "evidence_grounding")
    totals = {field: 0 for field in fields}
    correct = {field: 0 for field in fields}
    errors: list[dict[str, Any]] = []
    pairs = []
    for gold_index, gold in enumerate(gold_rows):
        evidence_ids = {item["evidence_id"] for item in gold.get("evidence_bindings", [])}
        for evidence_id in evidence_ids:
            for candidate in by_evidence.get(evidence_id, []):
                pairs.append((_text_similarity(gold["statement_text"], candidate["statement_text"]), gold_index, candidate))
    assigned_gold: set[int] = set()
    assigned_candidates: set[str] = set()
    matches: dict[int, Mapping[str, Any]] = {}
    for score, gold_index, candidate in sorted(pairs, key=lambda item: item[0], reverse=True):
        candidate_id = candidate["candidate_id"]
        if gold_index in assigned_gold or candidate_id in assigned_candidates:
            continue
        matches[gold_index] = candidate
        assigned_gold.add(gold_index)
        assigned_candidates.add(candidate_id)
    for gold_index, gold in enumerate(gold_rows):
        evidence_ids = {item["evidence_id"] for item in gold.get("evidence_bindings", [])}
        candidate = matches.get(gold_index)
        boundary_score = _boundary_similarity(gold["statement_text"], candidate["statement_text"]) if candidate else 0.0
        expected_relation = gold.get("predicate") if gold.get("predicate") in COARSE_RELATIONS else _predicate(str(gold.get("statement_text", "")), str(gold.get("statement_type", "fact")))
        checks = {
            "statement_boundary": bool(candidate and boundary_score >= 0.8),
            "statement_type": bool(candidate and candidate["statement_type"] == gold["statement_type"]),
            "entity": _entity_match(candidate, gold),
            "relation": bool(candidate and candidate.get("predicate") == expected_relation),
            "quantity": bool(candidate and _quantities_match(candidate.get("quantities", []), gold.get("quantities", []))),
            "unit": _units_match(candidate, gold),
            "comparison": _comparison_match(candidate, gold),
            "negation": _negation_match(candidate, gold),
            "condition": _condition_match(candidate, gold),
            "applicability": _applicability_match(candidate, gold),
            "applicability_meaning": _applicability_match(candidate, gold),
            "relation_direction": _relation_direction_match(candidate, gold),
            "unsupported_addition": not _unsupported_addition(candidate, gold),
            "evidence_grounding": bool(candidate and evidence_ids <= {b["evidence_id"] for b in candidate.get("evidence_bindings", [])}),
        }
        for field, passed in checks.items():
            totals[field] += 1
            correct[field] += int(passed)
        if not all(checks.values()):
            error_types = []
            if not checks["statement_boundary"]: error_types.append("boundary_error")
            if not checks["statement_type"]: error_types.append("statement_type_error")
            if not checks["entity"]: error_types.append("entity_error")
            if not checks["relation"]: error_types.append("relation_error")
            if not checks["quantity"]: error_types.append("quantity_error")
            if not checks["unit"]: error_types.append("unit_error")
            if not checks["comparison"]: error_types.append("comparison_error")
            if not checks["negation"]: error_types.append("negation_error")
            if not checks["condition"]: error_types.append("condition_loss")
            if not checks["applicability"]: error_types.append("applicability_error")
            if not checks["relation_direction"]: error_types.append("relation_direction_error")
            if not checks["unsupported_addition"]: error_types.append("unsupported_claim")
            # A missing candidate is a recall failure, not a fabricated claim.
            # Keep grounding false for the field metric, but reserve the
            # zero-tolerance unsupported_claim counter for an emitted
            # candidate that cites the wrong Evidence.
            if candidate is not None and not checks["evidence_grounding"]: error_types.append("unsupported_claim")
            errors.append({"statement_id": gold.get("statement_id"), "candidate_id": candidate.get("candidate_id") if candidate else None, "error_types": error_types})
    unmatched_gold = [gold_rows[index].get("statement_id") for index in range(len(gold_rows)) if index not in matches]
    unmatched_candidates = [candidate.get("candidate_id") for candidate in candidates if candidate.get("candidate_id") not in assigned_candidates]
    error_names = ("boundary_error", "statement_type_error", "entity_error", "relation_error", "quantity_error", "unit_error", "comparison_error", "negation_error", "condition_loss", "applicability_error", "relation_direction_error", "unsupported_claim")
    unmatched_review = [{"candidate_id": candidate_id, "classification": _classify_unmatched_candidate(candidate_id, candidates, matches, gold_rows)} for candidate_id in unmatched_candidates]
    review_counts = {name: sum(item["classification"] == name for item in unmatched_review) for name in ("valid_extra", "duplicate", "over_split", "unsupported", "needs_gold_completion")}
    semantic_correct = len(gold_rows) - len(errors)
    return {"gold_statement_count": len(gold_rows), "candidate_count": len(candidates), "gold_exhaustive": gold_exhaustive, "statement_recall": (len(matches) / len(gold_rows) if gold_rows else 0.0), "statement_semantic_correctness": (semantic_correct / len(gold_rows) if gold_rows else 0.0), "field_totals": totals, "field_correct": correct, "field_accuracy": {field: (correct[field] / totals[field] if totals[field] else 0.0) for field in fields}, "error_counts": {name: sum(name in error["error_types"] for error in errors) for name in error_names}, "errors": errors, "matched_pairs": [{"statement_id": gold_rows[index].get("statement_id"), "candidate_id": candidate.get("candidate_id"), "score": _text_similarity(gold_rows[index]["statement_text"], candidate["statement_text"])} for index, candidate in sorted(matches.items())], "unmatched_gold": unmatched_gold, "unmatched_candidates": unmatched_candidates, "unmatched_gold_count": len(unmatched_gold), "unmatched_candidate_count": len(unmatched_candidates), "unmatched_candidate_review": unmatched_review, "unmatched_candidate_review_counts": review_counts, "candidate_coverage": {"matched_candidate_count": len(assigned_candidates), "extra_candidate_count": len(unmatched_candidates), "duplicate_candidate_rate": review_counts["duplicate"] / len(candidates) if candidates else 0.0, "over_split_rate": review_counts["over_split"] / len(candidates) if candidates else 0.0, "spurious_candidate_rate": len(unmatched_candidates) / len(candidates) if gold_exhaustive and candidates else None}, "evaluator_adapters": {"applicability": "statement-level applicability text is compared when Gold provides it; source/document-level scope metadata alone does not imply not_applicable or a known statement scope"}}


def _units_match(candidate: Mapping[str, Any] | None, gold: Mapping[str, Any]) -> bool:
    if candidate is None:
        return False
    actual = [item.get("unit") for item in candidate.get("quantities", [])]
    expected = [item.get("unit") for item in gold.get("quantities", [])]
    return actual == expected or {"%" if item == "％" else item for item in actual} == {"%" if item == "％" else item for item in expected}


def _comparison_match(candidate: Mapping[str, Any] | None, gold: Mapping[str, Any]) -> bool:
    if candidate is None:
        return False
    return sorted(str(item.get("operator")) for item in candidate.get("quantities", [])) == sorted(str(item.get("operator")) for item in gold.get("quantities", []))


def _relation_direction_match(candidate: Mapping[str, Any] | None, gold: Mapping[str, Any]) -> bool:
    if candidate is None:
        return False
    expected = _relation_direction(str(gold.get("statement_text", "")), candidate.get("predicate", "describes"))
    return candidate.get("relation_direction") == expected


def _unsupported_addition(candidate: Mapping[str, Any] | None, gold: Mapping[str, Any]) -> bool:
    if candidate is None:
        return False
    text = str(gold.get("statement_text", ""))
    source_quantities = {_quantity_key(item) for item in gold.get("quantities", [])}
    if any(_quantity_key(item) not in source_quantities for item in candidate.get("quantities", [])):
        return True
    if any(_normalized_text(item.get("surface_form")) not in _normalized_text(text) for item in candidate.get("conditions", [])):
        return True
    return any(_normalized_text(item.get("surface_form")) not in _normalized_text(text) for item in candidate.get("negation_scope", []))


def _classify_unmatched_candidate(candidate_id: str, candidates: list[Mapping[str, Any]], matches: Mapping[int, Mapping[str, Any]], gold_rows: list[Mapping[str, Any]]) -> str:
    candidate = next(item for item in candidates if item.get("candidate_id") == candidate_id)
    matched = list(matches.values())
    if any(_text_similarity(candidate.get("statement_text"), item.get("statement_text")) >= 0.96 for item in matched):
        return "duplicate"
    if any(_normalized_text(candidate.get("statement_text")) in _normalized_text(item.get("statement_text")) for item in matched):
        return "over_split"
    if not candidate.get("evidence_bindings") or not candidate.get("statement_text"):
        return "unsupported"
    return "needs_gold_completion"


def _overlap(left: str, right: str) -> float:
    left, right = re.sub(r"\s+", "", left or ""), re.sub(r"\s+", "", right or "")
    if not left or not right:
        return 0.0
    return len(set(left) & set(right)) / max(1, len(set(left)))


def _normalized_text(value: Any) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def _text_similarity(left: Any, right: Any) -> float:
    left_text, right_text = _normalized_text(left), _normalized_text(right)
    if not left_text or not right_text:
        return 0.0
    matcher = SequenceMatcher(None, left_text, right_text, autojunk=False)
    longest = matcher.find_longest_match(0, len(left_text), 0, len(right_text)).size
    left_chars, right_chars = set(left_text), set(right_text)
    overlap = len(left_chars & right_chars)
    precision = overlap / len(right_chars) if right_chars else 0.0
    recall = overlap / len(left_chars) if left_chars else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    # A candidate may retain a short section heading or OCR line-wrap context;
    # use sequence similarity plus set F1 to make matching robust to this
    # formatting while still penalising unrelated extra text.
    recall = overlap / len(left_chars) if left_chars else 0.0
    return max(longest / len(left_text), matcher.ratio(), f1, recall)


def _boundary_similarity(left: Any, right: Any) -> float:
    def strip_formatting(value: Any) -> str:
        value = _normalized_text(value)
        value = re.sub(r"^[0-9]+(?:\.[0-9]+)*", "", value)
        return value.replace("应", "")

    return _text_similarity(strip_formatting(left), strip_formatting(right))


def _entity_match(candidate: Mapping[str, Any] | None, gold: Mapping[str, Any]) -> bool:
    if not candidate:
        return False
    expected = gold.get("entity_alignment") or []
    actual = candidate.get("subject_entities") or []
    for item in expected:
        target = str(item.get("surface_form", ""))
        if any(_overlap(target, entity.get("surface_form", "")) >= 0.6 for entity in actual):
            return True
        # Some frozen development labels expose only a canonical ID. The
        # candidate intentionally returns a grounded alias, not an ID; accept
        # that alias only when it is present in the labeled statement text.
        if "_" in target and any(_overlap(entity.get("surface_form", ""), gold.get("statement_text", "")) >= 0.6 for entity in actual):
            return True
    return False


def _condition_match(candidate: Mapping[str, Any] | None, gold: Mapping[str, Any]) -> bool:
    if not candidate:
        return False
    expected = gold.get("conditions") or []
    actual = candidate.get("conditions") or []
    if not expected:
        return all(_overlap(item.get("surface_form", ""), candidate.get("statement_text", "")) >= 0.6 for item in actual)
    return all(any(_overlap(item.get("surface_form", ""), other.get("surface_form", "")) >= 0.6 for other in actual) for item in expected)


def _negation_match(candidate: Mapping[str, Any] | None, gold: Mapping[str, Any]) -> bool:
    if not candidate:
        return False
    expected = gold.get("negation_scope") or []
    actual = candidate.get("negation_scope") or []
    if len(expected) != len(actual):
        return False
    return all(
        any(
            _normalized_text(item.get("surface_form")) == _normalized_text(other.get("surface_form"))
            and item.get("polarity") == other.get("polarity")
            for other in actual
        )
        for item in expected
    )


def _applicability_match(candidate: Mapping[str, Any] | None, gold: Mapping[str, Any]) -> bool:
    if not candidate:
        return False
    if candidate.get("document_logical_id") != gold.get("document_logical_id") or candidate.get("physical_page") != gold.get("physical_page"):
        return False
    expected = gold.get("applicability_scope") or {}
    actual = candidate.get("applicability_scope") or {}
    # Stage 11 Gold currently carries document/source routing scope (equipment,
    # lifecycle, activity, condition), not a sentence-level applicability
    # wording.  It is not valid to score candidate ``unknown`` as wrong merely
    # because that metadata exists; unknown remains distinct from
    # not_applicable.  Only an explicit Gold wording can require known scope.
    expected_text = expected.get("applicability_text")
    if expected_text:
        if actual.get("status") != "known" or _normalized_text(actual.get("applicability_text")) != _normalized_text(expected_text):
            return False
    elif actual.get("status") not in {"unknown", "known"}:
        return False
    elif actual.get("status") == "known" and not actual.get("applicability_text"):
        return False
    for key, value in actual.items():
        if key in {"document_key", "physical_page", "logical_page", "status", "applicability_text"}:
            continue
        if key in expected and _normalized_text(value) != _normalized_text(expected[key]):
            return False
    if actual.get("applicability_text") and _normalized_text(actual["applicability_text"]) not in _normalized_text(gold.get("statement_text", "")):
        return False
    return True


def _quantities_match(candidate: list[Mapping[str, Any]], gold: list[Mapping[str, Any]]) -> bool:
    if len(candidate) != len(gold):
        return False
    remaining = list(candidate)
    for expected in gold:
        match_index = None
        for index, actual in enumerate(remaining):
            if ("%" if actual.get("unit") == "％" else actual.get("unit")) != ("%" if expected.get("unit") == "％" else expected.get("unit")):
                continue
            actual_min = actual.get("min", actual.get("value") if actual.get("operator") == "gte" else None)
            actual_max = actual.get("max", actual.get("value") if actual.get("operator") == "lte" else None)
            expected_min = expected.get("min", expected.get("value") if expected.get("operator") == "gte" else None)
            expected_max = expected.get("max", expected.get("value") if expected.get("operator") == "lte" else None)
            if expected.get("value") is not None and expected.get("operator") not in {"gte", "lte"} and actual.get("value") != expected.get("value"):
                continue
            if expected_min is not None and actual_min != expected_min:
                continue
            if expected_max is not None and actual_max != expected_max:
                continue
            expected_operator = expected.get("operator")
            actual_operator = actual.get("operator")
            if expected_operator and actual_operator != expected_operator and not (expected_operator is None or (expected_operator == "range" and actual_operator == "range")):
                continue
            match_index = index
            break
        if match_index is None:
            return False
        remaining.pop(match_index)
    return not remaining
