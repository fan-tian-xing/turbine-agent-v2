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

    @classmethod
    def from_environment(cls, *, dotenv_path: Path | None = PROJECT_ROOT / ".env") -> "Settings":
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
        )
