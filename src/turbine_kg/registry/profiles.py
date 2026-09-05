"""Data-driven source profiles and processing defaults."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .schema import SOURCE_ROLES


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROFILE_PATH = PROJECT_ROOT / "config" / "source_profiles" / "registry.json"


@dataclass(frozen=True, slots=True)
class SourceProfile:
    profile_id: str
    source_role: str
    default_text_adapter: str


def load_source_profiles(path: Path = DEFAULT_PROFILE_PATH) -> tuple[dict[str, SourceProfile], list[dict[str, str]], str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    profiles = {
        profile_id: SourceProfile(
            profile_id=profile_id,
            source_role=record["source_role"],
            default_text_adapter=record.get("default_text_adapter", "auto"),
        )
        for profile_id, record in data["profiles"].items()
    }
    assignments = data["assignments"]
    default_profile_id = data["default_profile_id"]
    if default_profile_id not in profiles:
        raise ValueError(f"unknown default source profile: {default_profile_id}")
    for assignment in assignments:
        if assignment["profile_id"] not in profiles:
            raise ValueError(f"unknown source profile: {assignment['profile_id']}")
    invalid_roles = sorted({profile.source_role for profile in profiles.values()} - SOURCE_ROLES)
    if invalid_roles:
        raise ValueError(f"source profiles contain invalid source roles: {invalid_roles}")
    return profiles, assignments, default_profile_id


def profile_for_path(
    relative_path: str,
    profiles: dict[str, SourceProfile],
    assignments: list[dict[str, str]],
    default_profile_id: str,
) -> SourceProfile:
    for assignment in assignments:
        if relative_path.startswith(assignment["path_prefix"]):
            return profiles[assignment["profile_id"]]
    return profiles[default_profile_id]


def default_text_adapter_status(profile: SourceProfile, text_layer_status: str) -> str:
    if profile.default_text_adapter != "auto":
        return profile.default_text_adapter
    if text_layer_status == "native_text":
        return "native_text_available"
    if text_layer_status == "mixed_or_low_quality":
        return "text_review_required"
    return "ocr_required"
