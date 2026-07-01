"""Language support loaded from data files."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List


DATA_PATH = Path(__file__).with_name("i18n_profiles.json")


@dataclass(frozen=True)
class LanguageProfile:
    code: str
    label: str
    correction_markers: tuple[str, ...]
    constraint_markers: tuple[str, ...]
    plan_markers: tuple[str, ...]
    confirmation_markers: tuple[str, ...]
    concise_markers: tuple[str, ...]
    no_modification_markers: tuple[str, ...]
    recall_direct_markers: tuple[str, ...]
    recall_history_markers: tuple[str, ...]
    recall_topic_markers: tuple[str, ...]
    template: Dict[str, str]


@lru_cache(maxsize=1)
def _load_data() -> dict:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def _build_languages() -> Dict[str, LanguageProfile]:
    data = _load_data()
    templates = data["templates"]
    languages: Dict[str, LanguageProfile] = {}
    marker_fields = (
        "correction_markers",
        "constraint_markers",
        "plan_markers",
        "confirmation_markers",
        "concise_markers",
        "no_modification_markers",
        "recall_direct_markers",
        "recall_history_markers",
        "recall_topic_markers",
    )

    for item in data["languages"]:
        values = {field: tuple(item.get(field, [])) for field in marker_fields}
        template_name = item.get("template", data["default_template"])
        languages[item["code"]] = LanguageProfile(
            code=item["code"],
            label=item["label"],
            template=dict(templates[template_name]),
            **values,
        )
    return languages


LANGUAGES: Dict[str, LanguageProfile] = _build_languages()


def get_profile(code: str | None) -> LanguageProfile:
    data = _load_data()
    default_code = data["default_language"]
    return LANGUAGES.get(code or "", LANGUAGES[default_code])


def detect_language(text: str) -> str:
    data = _load_data()
    for rule in data.get("detection", []):
        pattern = rule.get("regex")
        if pattern and re.search(pattern, text):
            return rule["code"]

        tokens = rule.get("tokens") or []
        lower = text.lower()
        if tokens and any(token in lower for token in tokens):
            return rule["code"]

    return data["default_language"]


def detect_messages_language(messages: Iterable[dict]) -> str:
    user_text = "\n".join(
        str(message.get("content", ""))
        for message in messages
        if message.get("role") == "user"
    )
    return detect_language(user_text)


def all_markers(attr: str) -> List[str]:
    markers: List[str] = []
    for profile in LANGUAGES.values():
        markers.extend(getattr(profile, attr))
    return sorted(set(markers), key=len, reverse=True)
