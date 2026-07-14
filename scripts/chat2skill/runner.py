"""Orchestrates one extraction round trip against the cloud API.

Local responsibilities: parse the transcript, gather context (skills,
profile, history), call the API, persist the returned records, refresh
the project-level skill.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import api_client, storage
from .api_client import ApiError
from .config import llm_payload
from .memory_client import commit_transcript
from .maintenance import SkillMaintainer
from .models import Skill

PROJECT_SKILL_FILE = "PROJECT_SKILL.md"
PROJECT_SKILL_NAME = "project-skill"
LEGACY_PROJECT_SUMMARY_NAME = "project-chat2skill-summary"
PROJECT_SKILL_REQUEST_BYTES_LIMIT = 1_500_000
PROJECT_SKILL_CONTENT_LIMIT = 2200
PROJECT_SKILL_DESCRIPTION_LIMIT = 600
PROJECT_SKILL_MEMORY_ITEMS_PER_SKILL = 6
PROJECT_SKILL_MEMORY_TITLE_LIMIT = 160
PROJECT_SKILL_MEMORY_DESCRIPTION_LIMIT = 240
PROJECT_SKILL_MEMORY_CONTENT_LIMIT = 520
PROJECT_SKILL_MEMORY_EVIDENCE_LIMIT = 360
PROJECT_SKILL_MEMORY_TYPE_PRIORITY = {
    "constraint": 0,
    "specific_entity": 1,
    "failure_cause": 2,
    "failure_memory": 3,
    "success": 4,
}


def run_extraction(
    session_file: Path,
    user_id: str,
    config: dict,
    clean: bool = True,
    project_dir: str = "",
) -> dict:
    """Learn from one transcript with unified project memory + skills."""
    return commit_transcript(
        session_file=session_file,
        user_id=user_id,
        config=config,
        project_dir=project_dir,
        clean=clean,
    )


def rebuild_project_skill(
    user_id: str,
    config: dict,
    recent_messages: Optional[List[dict]] = None,
) -> Optional[Path]:
    """Refresh the project-level PROJECT_SKILL.md from active local skills."""
    storage.init_db()
    skills = [
        skill
        for skill in storage.load_skills(user_id, include_pending=False)
        if skill.status == "active" and skill.name not in {PROJECT_SKILL_NAME, LEGACY_PROJECT_SUMMARY_NAME}
    ]
    if not skills:
        return None

    memory_items_by_skill = storage.load_skill_memory_items(
        user_id,
        [skill.name for skill in skills],
    )
    payload_skills = [
        _project_skill_payload(skill, memory_items_by_skill.get(skill.name, []))
        for skill in skills
    ]
    source_memory_count = sum(len(skill.get("memory_items") or []) for skill in payload_skills)
    payload = {
        "user_id": user_id,
        "skills": payload_skills,
        "recent_messages": _compact_recent_messages(recent_messages or []),
        "existing_language": _existing_summary_language(user_id),
        "llm": llm_payload(config),
    }
    payload = _fit_project_skill_request(payload)
    try:
        response = api_client.project_skill(config["api_url"], payload)
    except (ApiError, TimeoutError, OSError) as exc:
        raise ApiError(f"project skill generation failed: {type(exc).__name__}: {exc}") from exc

    raw_content = response.get("content") if isinstance(response, dict) else None
    if not isinstance(raw_content, str) or not raw_content.strip():
        raise ApiError("project skill generation returned empty content")

    content = _normalize_project_skill_content(raw_content)
    if _looks_truncated_project_skill(content):
        raise ApiError("project skill generation returned incomplete content")

    out_dir = storage.SKILL_DIR / user_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / PROJECT_SKILL_FILE
    out_path.write_text(content, encoding="utf-8")
    storage.save_project_skill(
        user_id,
        content,
        file_path=out_path,
        source_skill_count=len(skills),
        source_memory_count=source_memory_count,
    )
    saved = storage.load_project_skill(user_id)
    if saved and saved.get("version") is not None:
        storage.save_project_skill_sources(
            user_id,
            int(saved["version"]),
            _project_skill_source_rows(skills, memory_items_by_skill),
        )
    return out_path


def rebuild_project_summary(
    user_id: str,
    config: dict,
    recent_messages: Optional[List[dict]] = None,
) -> Optional[Path]:
    """Compatibility wrapper for older callers."""
    return rebuild_project_skill(user_id, config, recent_messages)


def run_maintenance(user_id: str) -> dict:
    """Score, archive, and dedupe the local skill bank for one user."""
    storage.init_db()
    skills = storage.load_skills(user_id)
    usage = storage.load_usage_counts(user_id)
    report = SkillMaintainer().maintain(skills, usage)

    for loser, winner, similarity in report.merged:
        storage.absorb_skill_sources(winner, loser, user_id)
        storage.set_skill_status(loser, user_id, "archived", note=f"deduped_into:{winner}")

    for name in report.pruned:
        score = report.scores[name]
        storage.set_skill_status(name, user_id, "archived", note=f"pruned:score={score.total:.2f}")

    return {
        "kept": len(report.kept),
        "archived": report.pruned,
        "merged": [(loser, winner) for loser, winner, _ in report.merged],
    }


def _existing_summary_language(user_id: str) -> Optional[str]:
    import re

    project_skill = storage.load_project_skill(user_id)
    if project_skill and project_skill.get("language"):
        return str(project_skill["language"])

    path = storage.SKILL_DIR / user_id / PROJECT_SKILL_FILE
    if not path.exists():
        return None
    try:
        head = path.read_text(encoding="utf-8")[:2000]
    except OSError:
        return None
    match = re.search(r"^language:\s*([A-Za-z-]+)\s*$", head, flags=re.MULTILINE)
    return match.group(1) if match else None


def _project_skill_payload(skill: Skill, memory_items: Optional[list[dict]] = None) -> dict:
    return {
        "name": str(skill.name or ""),
        "description": _cap_text(str(skill.description or ""), PROJECT_SKILL_DESCRIPTION_LIMIT),
        "content": _cap_text(str(skill.content or ""), PROJECT_SKILL_CONTENT_LIMIT),
        "version": int(skill.version or 1),
        "skill_type": str(skill.skill_type or "preference"),
        "scope": str(skill.scope or "user"),
        "evidence_count": int(skill.evidence_count or 0),
        "confidence": float(skill.confidence or 0.0),
        "status": str(skill.status or "active"),
        "replay_score": float(skill.replay_score or 0.0),
        "replay_cases": int(skill.replay_cases or 0),
        "language": str(skill.language or "en"),
        "response_guard": dict(skill.response_guard or {}),
        "memory_items": _compact_project_skill_memory_items(memory_items or []),
    }


def _compact_recent_messages(messages: list[dict]) -> list[dict]:
    compact: list[dict] = []
    total_chars = 0
    for message in reversed(messages[-20:]):
        content = str(message.get("content") or "")[:4000]
        if not content:
            continue
        remaining = 40_000 - total_chars
        if remaining <= 0:
            break
        content = content[:remaining]
        compact.append({"role": str(message.get("role") or "user"), "content": content})
        total_chars += len(content)
    compact.reverse()
    return compact


def _fit_project_skill_request(payload: dict) -> dict:
    fitted = {
        **payload,
        "skills": [
            {**skill, "memory_items": list(skill.get("memory_items") or [])}
            for skill in payload.get("skills") or []
        ],
    }
    fitted["skills"].sort(key=_project_skill_payload_priority, reverse=True)
    if _request_size(fitted) <= PROJECT_SKILL_REQUEST_BYTES_LIMIT:
        return fitted

    for skill in fitted["skills"]:
        skill["memory_items"] = skill["memory_items"][:1]
    if _request_size(fitted) <= PROJECT_SKILL_REQUEST_BYTES_LIMIT:
        return fitted

    for skill in fitted["skills"]:
        skill["content"] = _cap_text(str(skill.get("content") or ""), 1200)
    while len(fitted["skills"]) > 1 and _request_size(fitted) > PROJECT_SKILL_REQUEST_BYTES_LIMIT:
        fitted["skills"].pop()
    return fitted


def _project_skill_payload_priority(skill: dict) -> tuple:
    guard = skill.get("response_guard") or {}
    has_guard = bool(isinstance(guard, dict) and guard.get("enabled"))
    has_constraint = any(
        isinstance(item, dict) and item.get("item_type") == "constraint"
        for item in skill.get("memory_items") or []
    )
    return (
        has_guard,
        has_constraint,
        float(skill.get("replay_score") or 0.0),
        float(skill.get("confidence") or 0.0),
        int(skill.get("evidence_count") or 0),
        int(skill.get("version") or 1),
    )


def _request_size(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _project_skill_source_rows(
    skills: list[Skill],
    memory_items_by_skill: dict[str, list[dict]],
) -> list[dict]:
    return [
        {
            "skill_name": skill.name,
            "skill_type": skill.skill_type,
            "confidence": skill.confidence,
            "evidence_count": skill.evidence_count,
            "source_memory_count": len(memory_items_by_skill.get(skill.name, [])),
        }
        for skill in skills
    ]


def _compact_project_skill_memory_items(items: list[dict]) -> list[dict]:
    ranked = sorted(
        items,
        key=lambda item: (
            PROJECT_SKILL_MEMORY_TYPE_PRIORITY.get(str(item.get("item_type") or ""), 99),
            -float(item.get("confidence") or 0.0),
            _created_at_desc_value(str(item.get("created_at") or "")),
        ),
    )
    compact = []
    for item in ranked[:PROJECT_SKILL_MEMORY_ITEMS_PER_SKILL]:
        compact.append(
            {
                "item_type": str(item.get("item_type") or ""),
                "title": _cap_text(str(item.get("title") or ""), PROJECT_SKILL_MEMORY_TITLE_LIMIT),
                "description": _cap_text(
                    str(item.get("description") or ""),
                    PROJECT_SKILL_MEMORY_DESCRIPTION_LIMIT,
                ),
                "content": _cap_text(
                    str(item.get("content") or ""),
                    PROJECT_SKILL_MEMORY_CONTENT_LIMIT,
                ),
                "evidence": _cap_text(
                    str(item.get("evidence") or ""),
                    PROJECT_SKILL_MEMORY_EVIDENCE_LIMIT,
                ),
                "source_session": str(item.get("source_session") or ""),
                "confidence": float(item.get("confidence") or 0.0),
            }
        )
    return compact


def _created_at_desc_value(value: str) -> float:
    if not value:
        return 0.0
    try:
        return -datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _normalize_project_skill_content(content: str) -> str:
    if not content.strip():
        return content
    normalized = content
    normalized = normalized.replace("name: project-chat2skill-summary", "name: project-skill", 1)
    return normalized


def _looks_truncated_project_skill(content: str) -> bool:
    stripped = content.rstrip()
    if not stripped:
        return True
    tail = stripped.splitlines()[-1].strip()
    if tail in {"-", "- **", "**", "##", "###"}:
        return True
    if tail.startswith("- **") and tail.count("**") == 1:
        return True
    if stripped.count("```") % 2:
        return True
    if stripped.count("---") < 2:
        return True
    return False


def _cap_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n...[truncated]"
