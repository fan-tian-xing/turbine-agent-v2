"""Shared identities and the monotonic budget of one answer."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .settings import PROJECT_ROOT


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json_text(value).encode("utf-8")).hexdigest()


def identity(kind: str, *values: Any) -> str:
    return kind + "-" + fingerprint(values)[:24]


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class BudgetExceeded(TimeoutError):
    pass


@dataclass
class Budget:
    seconds: float = 240
    max_rounds: int = 4
    calls_per_round: int = 6
    max_images: int = 4
    text_limit: int = 20_000
    statement_page: int = 40
    row_page: int = 20
    business_hops: int = 2
    started: float = field(default_factory=time.monotonic)
    calls: int = 0
    images: int = 0

    @classmethod
    def configured(cls) -> Budget:
        path = PROJECT_ROOT / "config/agent.json"
        values = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return cls(**values["budget"]) if "budget" in values else cls()

    def remaining(self) -> float:
        remaining = self.seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            raise BudgetExceeded("本次问答时间预算已用尽")
        return remaining

    def take(self, *, image: bool = False) -> None:
        self.remaining()
        if self.calls >= self.max_rounds * self.calls_per_round:
            raise BudgetExceeded("本次问答工具调用预算已用尽")
        if image and self.images >= self.max_images:
            raise BudgetExceeded("本次问答图片预算已用尽")
        self.calls += 1
        if image:
            self.images += 1


@dataclass(frozen=True)
class Snapshot:
    document_ids: tuple[str, ...]
    ontology_version: str
    skill_versions: tuple[str, ...]

    @property
    def key(self) -> str:
        return fingerprint((self.document_ids, self.ontology_version, self.skill_versions))
