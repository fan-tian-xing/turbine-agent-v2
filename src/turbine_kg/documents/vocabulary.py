"""Minimal machine-id to Chinese display mapping for the Stage 4 boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DisplayTerm:
    machine_id: str
    zh_cn: str
    definition: str
    synonyms: tuple[str, ...] = ()
    english_aliases: tuple[str, ...] = ()
    status: str = "seed"


def load_display_terms(path: Path) -> dict[str, DisplayTerm]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("terms"), list):
        raise ValueError("invalid display vocabulary contract")
    result: dict[str, DisplayTerm] = {}
    for item in payload["terms"]:
        machine_id = item.get("machine_id")
        if not isinstance(machine_id, str) or not machine_id or machine_id in result:
            raise ValueError("display vocabulary contains an invalid or duplicate machine_id")
        result[machine_id] = DisplayTerm(
            machine_id=machine_id,
            zh_cn=item["zh_cn"],
            definition=item["definition"],
            synonyms=tuple(item.get("synonyms", [])),
            english_aliases=tuple(item.get("english_aliases", [])),
            status=item.get("status", "seed"),
        )
    return result


def display_name(machine_id: str, terms: dict[str, DisplayTerm]) -> str:
    return terms.get(machine_id, DisplayTerm(machine_id, machine_id, "unregistered term")).zh_cn
