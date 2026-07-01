#!/usr/bin/env python3
"""UserPromptSubmit hook: inject learned skills into the conversation."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chat2skill.config import load_config
from chat2skill.memory_client import MemoryClientError, materialize_for_prompt
from chat2skill.hookio import (
    json_hook_output,
    log_event,
    project_dir_from_input,
    project_user_id,
    prompt_from_input,
    read_hook_input,
)
from chat2skill.context_store import context_key
from chat2skill.response_guard import reset_guard_state
from chat2skill.storage import record_skill_usage, save_project_memory_materialization


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

    return inject_memory_context(load_config(), project_dir, scoped_user_id, prompt)


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
            mode="unified",
            retrieved=0,
        )
        return 0

    context = (
        "## Chat2Skill Memory and Skills\n"
        "Apply this retrieved project memory, recall summary, and relevant skills when they match the current task:\n\n"
        f"{rendered}\n\n"
        f"Materialization ID: {result.get('materialization_id')}"
    )
    included_skills = (result.get("skills") or {}).get("skills_included") or []
    if included_skills:
        record_skill_usage(scoped_user_id, included_skills)
    save_project_memory_materialization(
        scoped_user_id,
        context_key(project_dir),
        {
            "materialization_id": result.get("materialization_id"),
            "memories_included": (result.get("memory") or {}).get("memories_included") or [],
            "skills_included": included_skills,
            "query": prompt,
            "rendered_prompt": context,
            "token_count": result.get("token_count"),
        },
    )
    json_hook_output(context)
    log_event(
        "UserPromptSubmit.done",
        project_dir=project_dir,
        user_id=scoped_user_id,
        mode="unified",
        materialization_id=result.get("materialization_id"),
        token_count=result.get("token_count"),
        coverage_score=(result.get("memory") or {}).get("coverage_score"),
        memory_retrieved=len((result.get("memory") or {}).get("memories_included") or []),
        skills=included_skills,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
