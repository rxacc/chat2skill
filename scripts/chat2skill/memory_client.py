"""Unified Memory backend adapter for Chat2Skill hooks.

This adapter matches the stateless c2s-algorithm API:
- local plugin storage owns memory state under ~/.chat2skill/contexts/
- cloud API runs /v1/unified/learn and /v1/unified/retrieve
- returned memory delta and skill updates are persisted locally by the plugin
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import api_client, storage
from .config import llm_payload
from .context_store import (
    apply_memory_result,
    context_state,
    load_context,
    save_context,
    save_materialization,
)
from .models import Skill, UserModel
from .transcripts import parse_transcript


class MemoryClientError(Exception):
    pass


def materialize_for_prompt(
    config: dict,
    project_dir: str,
    prompt: str,
    user_id: str,
) -> dict[str, Any]:
    """Return unified prompt-ready memory + skills for the current prompt."""
    storage.init_db()
    context = load_context(project_dir, user_id)
    skills = storage.load_skills(user_id, include_pending=False)
    payload = {
        "user_id": user_id,
        "query": prompt,
        "existing_memory": context_state(context),
        "existing_skills": [skill.to_dict() for skill in skills],
        "user_profile": storage.load_user_profile(user_id).to_dict(),
        "token_budget": int((config.get("memory") or {}).get("token_budget") or 4000),
        "memory_ratio": float((config.get("memory") or {}).get("memory_ratio") or 0.6),
        "skill_top_k": int((config.get("memory") or {}).get("skill_top_k") or 6),
        "target_model": (config.get("memory") or {}).get("target_model") or "generic",
        "llm": llm_payload(config),
    }
    try:
        result = api_client.unified_retrieve(config["api_url"], payload)
    except api_client.ApiError as exc:
        raise MemoryClientError(str(exc)) from None

    save_materialization(context, result, prompt)
    save_context(project_dir, user_id, context)
    return result


def commit_transcript(
    session_file: Path,
    user_id: str,
    config: dict,
    project_dir: str = "",
    clean: bool = True,
) -> dict[str, Any]:
    """Commit one transcript to unified memory + skill learning."""
    messages = parse_transcript(session_file, clean=clean)
    if len(messages) < 2:
        return {"status": "skipped", "backend": "memory", "reason": "too_few_messages"}

    storage.init_db()
    session_id = session_file.stem
    storage.save_conversation(session_id, user_id, messages)

    context = load_context(project_dir, user_id)
    existing_skills = storage.load_skills(user_id)
    profile = storage.load_user_profile(user_id)
    payload = {
        "session_id": session_id,
        "user_id": user_id,
        "agent_id": (config.get("memory") or {}).get("agent_id") or "chat2skill",
        "messages": messages,
        "feedback": None,
        "existing_memory": context_state(context),
        "existing_skills": [skill.to_dict() for skill in existing_skills],
        "user_profile": profile.to_dict(),
        "llm": llm_payload(config),
    }
    try:
        response = api_client.unified_learn(config["api_url"], payload)
    except api_client.ApiError as exc:
        raise MemoryClientError(str(exc)) from None

    memory = response.get("memory") or {}
    apply_memory_result(context, memory)
    context_path = save_context(project_dir, user_id, context)

    skill_status = _persist_skill_response(response.get("skills") or {}, user_id)
    return {
        "status": skill_status["status"],
        "backend": "memory",
        "memory": {
            "context_path": str(context_path),
            "bullets_added": memory.get("bullets_added", 0),
            "bullets_updated": memory.get("bullets_updated", 0),
            "bullets_removed": memory.get("bullets_removed", 0),
            "bullets_merged": memory.get("bullets_merged", 0),
            "reason": memory.get("reason"),
        },
        "skill": skill_status.get("skill"),
        "skill_status": skill_status.get("skill_status"),
        "llm_used": response.get("llm_used"),
    }


def _persist_skill_response(skills: dict[str, Any], user_id: str) -> dict[str, Any]:
    updated_profile = skills.get("updated_profile")
    if isinstance(updated_profile, dict):
        storage.save_user_profile(UserModel.from_dict(updated_profile))

    skill_data = skills.get("skill")
    if not skill_data:
        return {"status": "memory_saved", "reason": skills.get("reason")}

    skill = Skill.from_dict(skill_data)
    if skill.status == "rejected":
        return {"status": "rejected", "skill": skill.name, "skill_status": skill.status}

    storage.save_skill(skill, user_id=user_id)
    return {"status": "saved", "skill": skill.name, "skill_status": skill.status}
