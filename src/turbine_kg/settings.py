"""Centralized local configuration without module-level absolute paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    source_root: Path
    neo4j_uri: str = "neo4j://localhost:7688"
    neo4j_user: str = "neo4j"
    neo4j_password: str | None = None

    @classmethod
    def from_environment(cls) -> "Settings":
        source_value = os.environ.get("SOURCE_ROOT", "../Original materials")
        source_root = Path(source_value)
        if not source_root.is_absolute():
            source_root = PROJECT_ROOT / source_root

        return cls(
            source_root=source_root.resolve(),
            neo4j_uri=os.environ.get("NEO4J_URI", "neo4j://localhost:7688"),
            neo4j_user=os.environ.get("NEO4J_USER", "neo4j"),
            neo4j_password=os.environ.get("NEO4J_PASSWORD"),
        )
