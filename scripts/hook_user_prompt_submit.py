#!/usr/bin/env python3
"""UserPromptSubmit hook: inject learned skills into the conversation."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chat2skill.config import backend_name, base_user_id, load_config
from chat2skill.memory_client import MemoryClientError, materialize_for_prompt
from chat2skill.hookio import (
    json_hook_output,
    log_event,
    project_dir_from_input,
    project_user_id,
    prompt_from_input,
    read_hook_input,
)
from chat2skill.response_guard import reset_guard_state
from chat2skill.retrieval import SkillRetriever
from chat2skill.runner import PROJECT_SUMMARY_FILE, PROJECT_SUMMARY_NAME
from chat2skill.storage import SKILL_DIR, init_db, load_skills, record_skill_usage

DETAIL_SKILL_TOP_K = 5
DETAIL_SKILL_CHAR_LIMIT = 1800
DETAIL_TOTAL_CHAR_LIMIT = 7000


def main() -> int:
    data = read_hook_input()
    prompt = prompt_from_input(data)
    project_dir = project_dir_from_input(data)
    scoped_user_id = project_user_id(project_dir)
    reset_guard_state(scoped_user_id)
    log_event(
        "UserPromptSubmit.start",
        project_dir=project_dir,
        user_id=scoped_user_id,
        prompt_preview=prompt[:160],
    )

    config = load_config()
    if backend_name(config) == "memory":
        return inject_memory_context(config, project_dir, scoped_user_id, prompt)
    return inject_chat2skill_context(scoped_user_id, project_dir, prompt)


def inject_memory_context(
    config: dict,
    project_dir: str,
    scoped_user_id: str,
    prompt: str,
) -> int:
    try:
        result = materialize_for_prompt(config, project_dir, prompt, scoped_user_id)
    except MemoryClientError as exc:
        log_event(
            "UserPromptSubmit.memory_failed",
            project_dir=project_dir,
            user_id=scoped_user_id,
            error=str(exc),
        )
        return 0

    rendered = str(result.get("rendered_text") or "").strip()
    if not rendered:
        log_event(
            "UserPromptSubmit.done",
            project_dir=project_dir,
            user_id=scoped_user_id,
            backend="memory",
            retrieved=0,
        )
        return 0

    context = (
        "## Chat2Skill Memory and Skills\n"
        "Apply this retrieved project memory and relevant skills when they match the current task:\n\n"
        f"{rendered}\n\n"
        f"Materialization ID: {result.get('materialization_id')}"
    )
    included_skills = (result.get("skills") or {}).get("skills_included") or []
    if included_skills:
        record_skill_usage(scoped_user_id, included_skills)
    json_hook_output(context)
    log_event(
        "UserPromptSubmit.done",
        project_dir=project_dir,
        user_id=scoped_user_id,
        backend="memory",
        materialization_id=result.get("materialization_id"),
        token_count=result.get("token_count"),
        coverage_score=(result.get("memory") or {}).get("coverage_score"),
        memory_retrieved=len((result.get("memory") or {}).get("bullets_included") or []),
        skills=included_skills,
    )
    return 0


def inject_chat2skill_context(scoped_user_id: str, project_dir: str, prompt: str) -> int:
    init_db()
    project_skill_path = SKILL_DIR / scoped_user_id / PROJECT_SUMMARY_FILE
    project_skill = ""
    if project_skill_path.exists():
        project_skill = project_skill_path.read_text(encoding="utf-8").strip()

    default_user = base_user_id()
    project_skills = [
        skill
        for skill in load_skills(scoped_user_id, include_pending=False)
        if skill.name != PROJECT_SUMMARY_NAME
    ]
    skills = list(project_skills)
    owners = {s.name: scoped_user_id for s in skills}
    if default_user != scoped_user_id:
        for skill in load_skills(default_user, include_pending=False):
            owners.setdefault(skill.name, default_user)
            skills.append(skill)

    retriever = SkillRetriever()
    retrieved = retriever.retrieve(prompt, skills, top_k=DETAIL_SKILL_TOP_K, active_only=True)
    if not retrieved and not project_skill:
        log_event(
            "UserPromptSubmit.done",
            project_dir=project_dir,
            user_id=scoped_user_id,
            retrieved=0,
        )
        return 0

    by_owner: dict[str, list[str]] = {}
    for item in retrieved:
        owner = owners.get(item.skill.name, scoped_user_id)
        by_owner.setdefault(owner, []).append(item.skill.name)
    if project_skill:
        by_owner.setdefault(scoped_user_id, []).extend(skill.name for skill in project_skills)
    for owner, names in by_owner.items():
        record_skill_usage(owner, names)

    context_parts = []
    if project_skill:
        context_parts.append(
            "## Chat2Skill Project Summary\n"
            "Apply this project-level summary when relevant:\n\n"
            f"{project_skill}"
        )
    if retrieved:
        context_parts.append(
            "## Chat2Skill Retrieved Detailed Skills\n"
            "These are the concrete skills most relevant to the current prompt. "
            "Use their checklist/procedure/pitfalls as binding task guidance when relevant:\n\n"
            f"{format_detailed_skills(retrieved)}"
        )
    context_parts.append(f"Project skill namespace: {scoped_user_id}")
    json_hook_output("\n\n".join(context_parts))
    log_event(
        "UserPromptSubmit.done",
        project_dir=project_dir,
        user_id=scoped_user_id,
        backend="chat2skill",
        retrieved=len(retrieved) + (1 if project_skill else 0),
        included_project_summary=bool(project_skill),
        skills=[item.skill.name for item in retrieved],
    )
    return 0


def format_detailed_skills(retrieved) -> str:
    sections = []
    used_chars = 0
    for item in retrieved:
        skill = item.skill
        content = (skill.content or "").strip()
        if len(content) > DETAIL_SKILL_CHAR_LIMIT:
            content = content[:DETAIL_SKILL_CHAR_LIMIT].rstrip() + "\n...[truncated]"
        section = (
            f"### {skill.name} score={item.score:.3f}\n"
            f"Description: {skill.description}\n\n"
            f"{content}"
        )
        if used_chars + len(section) > DETAIL_TOTAL_CHAR_LIMIT and sections:
            break
        sections.append(section)
        used_chars += len(section)
    return "\n\n".join(sections)


if __name__ == "__main__":
    raise SystemExit(main())
