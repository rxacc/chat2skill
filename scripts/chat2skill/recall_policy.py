"""Policy for deciding when a prompt needs query-time recall synthesis."""

from __future__ import annotations

from .i18n import all_markers


def should_synthesize_recall(prompt: str) -> bool:
    text = _normalize(prompt)
    if not text:
        return False

    if _contains_any(text, all_markers("recall_direct_markers")):
        return True

    return _contains_any(text, all_markers("recall_history_markers")) and _contains_any(
        text,
        all_markers("recall_topic_markers"),
    )


def _normalize(text: str) -> str:
    return " ".join(str(text or "").casefold().split())


def _contains_any(text: str, markers: list[str]) -> bool:
    return any(marker and marker.casefold() in text for marker in markers)
