"""Unified memory and skills adapter for Chat2Skill hooks.

This adapter matches the stateless c2s-algorithm API:
- local plugin storage owns memory state in ~/.chat2skill/c2s.db
- cloud API runs /v1/unified/learn on compact caller-provided context
- returned memory delta and skill updates are persisted locally by the plugin
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from . import api_client, storage
from .config import llm_payload
from .context_store import (
    apply_memory_result,
    context_key,
    context_state,
    load_context,
    save_context,
    save_materialization,
)
from .models import Skill, UserModel
from .recall_policy import should_synthesize_recall
from .retrieval import MemoryRetriever, SkillRetriever
from .transcripts import parse_transcript


class MemoryClientError(Exception):
    pass


DEFAULT_TOKEN_BUDGET = 4000
DEFAULT_MEMORY_RATIO = 0.6
DEFAULT_PROMPT_MEMORY_TOP_K = 12
DEFAULT_PROMPT_SKILL_TOP_K = 6
DEFAULT_RECALL_SYNTHESIS_MEMORY_TOP_K = 32
DEFAULT_RECALL_SYNTHESIS_SKILL_TOP_K = 8
DEFAULT_RECALL_SYNTHESIS_TOKEN_BUDGET = 1200
DEFAULT_LEARN_MEMORY_TOP_K = 40
DEFAULT_LEARN_SKILL_TOP_K = 20
DEFAULT_LEARN_MAX_MESSAGES = 120
DEFAULT_LEARN_MESSAGE_CHAR_LIMIT = 6000
DEFAULT_LEARN_TOTAL_CHAR_LIMIT = 90000
SKILL_CONTENT_CHAR_LIMIT = 2400
MEMORY_CONTENT_CHAR_LIMIT = 1200
CORE_MEMORY_CHAR_LIMIT = 5000


def materialize_for_prompt(
    config: dict,
    project_dir: str,
    prompt: str,
    user_id: str,
) -> dict[str, Any]:
    """Return prompt-ready memory + skills from local c2s.db.

    The privacy contract keeps long-lived user data local. Prompt retrieval
    therefore does not call the cloud API; the cloud is used for stateless
    learn/extract calls only.
    """
    storage.init_db()
    context = load_context(project_dir, user_id)
    skills = storage.load_skills(user_id, include_pending=False)

    options = _memory_options(config)
    retrieved_memories = MemoryRetriever().retrieve(
        prompt,
        context.get("memories") or [],
        top_k=options["prompt_memory_top_k"],
        active_only=True,
    )
    retrieved_skills = SkillRetriever().retrieve(
        prompt,
        skills,
        top_k=options["skill_top_k"],
        active_only=True,
    )

    result = _build_local_materialization(
        context=context,
        retrieved_memories=retrieved_memories,
        retrieved_skills=retrieved_skills,
        token_budget=options["token_budget"],
        memory_ratio=options["memory_ratio"],
    )
    recall_synthesis = _recall_synthesis_for_prompt(
        config=config,
        project_dir=project_dir,
        prompt=prompt,
        user_id=user_id,
        context=context,
        skills=skills,
        options=options,
    )
    if recall_synthesis:
        result = _prepend_recall_synthesis(result, recall_synthesis)
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
        return {"status": "skipped", "mode": "unified", "reason": "too_few_messages"}

    storage.init_db()
    session_id = session_file.stem
    storage.save_conversation(session_id, user_id, messages)

    context = load_context(project_dir, user_id)
    options = _memory_options(config)
    task_text = _messages_text(messages)
    existing_memory = _context_state_for_learn(context, task_text, options)
    existing_skills = _skills_for_learn(user_id, task_text, options)
    profile = storage.load_user_profile(user_id)
    payload = {
        "session_id": session_id,
        "user_id": user_id,
        "agent_id": (config.get("memory") or {}).get("agent_id") or "chat2skill",
        "messages": _trim_messages_for_learn(messages, options),
        "feedback": None,
        "existing_memory": existing_memory,
        "existing_skills": [_skill_payload_for_learn(skill) for skill in existing_skills],
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
    storage.record_project_memory_activity(user_id, context_key(project_dir), session_id, memory)

    skill_status = _persist_skill_response(response.get("skills") or {}, user_id)
    return {
        "status": skill_status["status"],
        "mode": "unified",
        "memory": {
            "context_path": str(context_path),
            "memories_added": memory.get("memories_added", 0),
            "memories_updated": memory.get("memories_updated", 0),
            "memories_removed": memory.get("memories_removed", 0),
            "memories_merged": memory.get("memories_merged", 0),
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


def _memory_options(config: dict) -> dict[str, Any]:
    memory = dict(config.get("memory") or {})
    token_budget = int(memory.get("token_budget") or DEFAULT_TOKEN_BUDGET)
    memory_ratio = float(memory.get("memory_ratio") or DEFAULT_MEMORY_RATIO)
    return {
        "token_budget": token_budget,
        "memory_ratio": memory_ratio,
        "prompt_memory_top_k": int(memory.get("prompt_memory_top_k") or DEFAULT_PROMPT_MEMORY_TOP_K),
        "skill_top_k": int(memory.get("skill_top_k") or DEFAULT_PROMPT_SKILL_TOP_K),
        "recall_synthesis_memory_top_k": int(
            memory.get("recall_synthesis_memory_top_k") or DEFAULT_RECALL_SYNTHESIS_MEMORY_TOP_K
        ),
        "recall_synthesis_skill_top_k": int(
            memory.get("recall_synthesis_skill_top_k") or DEFAULT_RECALL_SYNTHESIS_SKILL_TOP_K
        ),
        "recall_synthesis_token_budget": int(
            memory.get("recall_synthesis_token_budget") or DEFAULT_RECALL_SYNTHESIS_TOKEN_BUDGET
        ),
        "learn_memory_top_k": int(memory.get("learn_memory_top_k") or DEFAULT_LEARN_MEMORY_TOP_K),
        "learn_skill_top_k": int(memory.get("learn_skill_top_k") or DEFAULT_LEARN_SKILL_TOP_K),
        "learn_max_messages": int(memory.get("learn_max_messages") or DEFAULT_LEARN_MAX_MESSAGES),
        "learn_message_char_limit": int(
            memory.get("learn_message_char_limit") or DEFAULT_LEARN_MESSAGE_CHAR_LIMIT
        ),
        "learn_total_char_limit": int(
            memory.get("learn_total_char_limit") or DEFAULT_LEARN_TOTAL_CHAR_LIMIT
        ),
    }


def _recall_synthesis_for_prompt(
    *,
    config: dict,
    project_dir: str,
    prompt: str,
    user_id: str,
    context: dict[str, Any],
    skills: list[Skill],
    options: dict[str, Any],
) -> dict[str, Any] | None:
    if not should_synthesize_recall(prompt):
        return None

    retrieved_memories = MemoryRetriever().retrieve(
        prompt,
        context.get("memories") or [],
        top_k=options["recall_synthesis_memory_top_k"],
        active_only=True,
    )
    retrieved_skills = SkillRetriever().retrieve(
        prompt,
        skills,
        top_k=options["recall_synthesis_skill_top_k"],
        active_only=True,
    )
    payload = {
        "user_id": user_id,
        "query": prompt,
        "existing_memory": {
            "core_memory": _cap_chars(str(context.get("core_memory") or ""), CORE_MEMORY_CHAR_LIMIT),
            "memories": [_memory_payload_for_learn(item.memory) for item in retrieved_memories],
            "schemas": _schemas_for_memories(
                context.get("schemas") or [],
                {str(item.memory.get("id")) for item in retrieved_memories if item.memory.get("id")},
            ),
        },
        "existing_skills": [_skill_payload_for_learn(item.skill) for item in retrieved_skills],
        "user_profile": storage.load_user_profile(user_id).to_dict(),
        "token_budget": options["recall_synthesis_token_budget"],
        "max_memories": options["recall_synthesis_memory_top_k"],
        "max_skills": options["recall_synthesis_skill_top_k"],
        "target_model": (config.get("memory") or {}).get("target_model") or "generic",
        "llm": llm_payload(config),
    }
    try:
        return api_client.unified_recall_synthesize(config["api_url"], payload)
    except api_client.ApiError:
        return None


def _prepend_recall_synthesis(result: dict[str, Any], synthesis: dict[str, Any]) -> dict[str, Any]:
    summary = str(synthesis.get("recall_summary") or "").strip()
    if not summary:
        return result

    merged = dict(result)
    section = "## Chat2Skill Recall Summary\n" + summary
    rendered = str(merged.get("rendered_text") or "").strip()
    merged["rendered_text"] = section + ("\n\n" + rendered if rendered else "")
    merged["token_count"] = _estimate_tokens(merged["rendered_text"])
    merged["recall_synthesis"] = {
        "llm_used": bool(synthesis.get("llm_used")),
        "memories_included": synthesis.get("memories_included") or [],
        "skills_included": synthesis.get("skills_included") or [],
        "token_count": synthesis.get("token_count"),
    }

    memory = dict(merged.get("memory") or {})
    existing_memory_ids = list(memory.get("memories_included") or [])
    for memory_id in synthesis.get("memories_included") or []:
        if memory_id not in existing_memory_ids:
            existing_memory_ids.append(memory_id)
    memory["memories_included"] = existing_memory_ids
    merged["memory"] = memory

    skills = dict(merged.get("skills") or {})
    existing_skill_ids = list(skills.get("skills_included") or [])
    for skill_name in synthesis.get("skills_included") or []:
        if skill_name not in existing_skill_ids:
            existing_skill_ids.append(skill_name)
    skills["skills_included"] = existing_skill_ids
    merged["skills"] = skills
    return merged


def _build_local_materialization(
    *,
    context: dict[str, Any],
    retrieved_memories: list,
    retrieved_skills: list,
    token_budget: int,
    memory_ratio: float,
) -> dict[str, Any]:
    memory_budget = int(token_budget * memory_ratio)
    skill_budget = max(200, token_budget - memory_budget)
    core_memory = str(context.get("core_memory") or "").strip()
    memory_text = MemoryRetriever().format_for_prompt(retrieved_memories)
    skills_text = _format_skills_for_prompt(retrieved_skills)

    memory_parts = []
    if core_memory:
        memory_parts.append("## Project Core Memory\n" + _cap_text(core_memory, memory_budget // 2))
    if memory_text:
        memory_parts.append(
            "## Relevant Project Memories\n"
            + _cap_text(memory_text, max(200, memory_budget - _estimate_tokens(core_memory)))
        )

    prompt_parts = []
    if memory_parts:
        prompt_parts.append("\n\n".join(memory_parts))
    if skills_text:
        prompt_parts.append("## Relevant Project Skills\n" + _cap_text(skills_text, skill_budget))

    rendered = "\n\n".join(part for part in prompt_parts if part.strip())
    rendered = _cap_text(rendered, token_budget)
    materialization_id = str(uuid.uuid4())
    return {
        "schema_version": "1",
        "rendered_text": rendered,
        "token_count": _estimate_tokens(rendered),
        "materialization_id": materialization_id,
        "memory": {
            "rendered_text": "\n\n".join(memory_parts),
            "memories_included": [
                str(item.memory.get("id"))
                for item in retrieved_memories
                if item.memory.get("id")
            ],
            "schemas_included": [],
            "token_count": _estimate_tokens("\n\n".join(memory_parts)),
            "coverage_score": 1.0 if rendered else 0.0,
        },
        "skills": {
            "skills_included": [item.skill.name for item in retrieved_skills],
            "token_count": _estimate_tokens(skills_text),
        },
    }


def _context_state_for_learn(
    context: dict[str, Any],
    task_text: str,
    options: dict[str, Any],
) -> dict[str, Any]:
    state = context_state(context)
    retrieved = MemoryRetriever().retrieve(
        task_text,
        state.get("memories") or [],
        top_k=options["learn_memory_top_k"],
        active_only=True,
    )
    state["core_memory"] = _cap_chars(str(state.get("core_memory") or ""), CORE_MEMORY_CHAR_LIMIT)
    state["memories"] = [_memory_payload_for_learn(item.memory) for item in retrieved]
    state["schemas"] = _schemas_for_memories(
        state.get("schemas") or [],
        {str(item.memory.get("id")) for item in retrieved if item.memory.get("id")},
    )
    return state


def _schemas_for_memories(schemas: list[dict], memory_ids: set[str]) -> list[dict]:
    selected = []
    for schema in schemas:
        ids = {str(item) for item in schema.get("memory_ids") or []}
        if ids & memory_ids:
            selected.append(schema)
    return selected[:10]


def _skills_for_learn(user_id: str, task_text: str, options: dict[str, Any]) -> list[Skill]:
    skills = storage.load_skills(user_id, include_pending=False)
    retrieved = SkillRetriever().retrieve(
        task_text,
        skills,
        top_k=options["learn_skill_top_k"],
        active_only=True,
    )
    return [item.skill for item in retrieved]


def _skill_payload_for_learn(skill: Skill) -> dict[str, Any]:
    payload = skill.to_dict()
    payload["content"] = _cap_chars(str(payload.get("content") or ""), SKILL_CONTENT_CHAR_LIMIT)
    payload["embedding_vector"] = []
    payload["memory_items"] = []
    return payload


def _memory_payload_for_learn(memory: dict[str, Any]) -> dict[str, Any]:
    payload = dict(memory)
    payload["content"] = _cap_chars(str(payload.get("content") or ""), MEMORY_CONTENT_CHAR_LIMIT)
    payload["embedding"] = []
    return payload


def _trim_messages_for_learn(messages: list[dict], options: dict[str, Any]) -> list[dict]:
    max_messages = max(2, int(options["learn_max_messages"]))
    char_limit = max(500, int(options["learn_message_char_limit"]))
    total_limit = max(2000, int(options["learn_total_char_limit"]))
    selected = messages[-max_messages:]
    trimmed = []
    used = 0
    for message in selected:
        content = _cap_chars(str(message.get("content") or ""), char_limit)
        if used + len(content) > total_limit and len(trimmed) >= 2:
            break
        used += len(content)
        item = dict(message)
        item["content"] = content
        trimmed.append(item)
    return trimmed


def _messages_text(messages: list[dict]) -> str:
    return "\n".join(
        str(message.get("content") or "")
        for message in messages
        if message.get("content")
    )


def _format_skills_for_prompt(retrieved: list) -> str:
    sections = []
    for item in retrieved:
        skill = item.skill
        content = _cap_chars((skill.content or "").strip(), SKILL_CONTENT_CHAR_LIMIT)
        sections.append(
            f"### {skill.name} score={item.score:.3f}\n"
            f"Description: {skill.description}\n\n"
            f"{content}"
        )
    return "\n\n".join(sections)


def _cap_text(text: str, token_budget: int) -> str:
    return _cap_chars(text, max(0, token_budget) * 4)


def _cap_chars(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars)].rstrip() + "\n...[truncated]"


def _estimate_tokens(text: str) -> int:
    return max(0, len(text) // 4)
