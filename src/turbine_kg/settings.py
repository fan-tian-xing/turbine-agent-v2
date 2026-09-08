"""Centralized local configuration without module-level absolute paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read_dotenv(path: Path) -> dict[str, str]:
    """Read the small, KEY=VALUE subset used by the project .env file."""
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    source_root: Path
    ocr_derived_root: Path
    neo4j_uri: str = "neo4j://localhost:7688"
    neo4j_user: str = "neo4j"
    neo4j_password: str | None = None
    neo4j_database: str = "neo4j"
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_timeout_seconds: float = 60
    llm_allow_evidence_send: bool = False
    llm_fallback_base_url: str = ""
    llm_fallback_model: str = ""
    llm_fallback_api_key: str = ""
    llm_fallback_timeout_seconds: float = 60
    llm_fallback_2_base_url: str = ""
    llm_fallback_2_model: str = ""
    llm_fallback_2_api_key: str = ""
    llm_fallback_2_timeout_seconds: float = 60

    @classmethod
    def from_environment(
        cls,
        *,
        dotenv_path: Path | None = PROJECT_ROOT / ".env",
    ) -> "Settings":
        environment = _read_dotenv(dotenv_path) if dotenv_path is not None else {}
        environment.update(os.environ)

        source_value = environment.get("SOURCE_ROOT", "../Original materials")
        source_root = Path(source_value)
        if not source_root.is_absolute():
            source_root = PROJECT_ROOT / source_root
        ocr_value = environment.get("OCR_DERIVED_ROOT", "var/derived/ocr")
        ocr_derived_root = Path(ocr_value)
        if not ocr_derived_root.is_absolute():
            ocr_derived_root = PROJECT_ROOT / ocr_derived_root

        return cls(
            source_root=source_root.resolve(),
            ocr_derived_root=ocr_derived_root.resolve(),
            neo4j_uri=environment.get("NEO4J_URI", "neo4j://localhost:7688"),
            neo4j_user=environment.get("NEO4J_USER", "neo4j"),
            neo4j_password=environment.get("NEO4J_PASSWORD"),
            neo4j_database=environment.get("NEO4J_DATABASE", "neo4j"),
            llm_base_url=environment.get("LLM_BASE_URL", ""),
            llm_model=environment.get("LLM_MODEL", ""),
            llm_api_key=environment.get("LLM_API_KEY", environment.get("OPENAI_API_KEY", "")),
            llm_timeout_seconds=float(environment.get("LLM_TIMEOUT_SECONDS", "60")),
            llm_allow_evidence_send=environment.get("LLM_ALLOW_EVIDENCE_SEND", "false").lower() == "true",
            llm_fallback_base_url=environment.get("LLM_FALLBACK_BASE_URL", ""),
            llm_fallback_model=environment.get("LLM_FALLBACK_MODEL", ""),
            llm_fallback_api_key=environment.get("LLM_FALLBACK_API_KEY", ""),
            llm_fallback_timeout_seconds=float(environment.get("LLM_FALLBACK_TIMEOUT_SECONDS", "60")),
            llm_fallback_2_base_url=environment.get("LLM_FALLBACK_2_BASE_URL", ""),
            llm_fallback_2_model=environment.get("LLM_FALLBACK_2_MODEL", ""),
            llm_fallback_2_api_key=environment.get("LLM_FALLBACK_2_API_KEY", ""),
            llm_fallback_2_timeout_seconds=float(environment.get("LLM_FALLBACK_2_TIMEOUT_SECONDS", "60")),
        )
