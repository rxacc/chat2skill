#!/usr/bin/env python3
"""CLI: retrieve learned skills for a task and print a prompt snippet.

For agents without hook support: call this before answering and inject
the printed snippet into the working context.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chat2skill.config import DATA_HOME, backend_name, base_user_id, load_config
from chat2skill.memory_client import MemoryClientError, materialize_for_prompt
from chat2skill.retrieval import SkillRetriever
from chat2skill.storage import init_db, load_skills, record_skill_usage


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrieve learned Chat2Skill skills.")
    parser.add_argument("task", nargs="+", help="Current user task text.")
    parser.add_argument("--user-id", default=base_user_id())
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--project-dir", default=os.getcwd())
    args = parser.parse_args()

    task = " ".join(args.task)
    config = load_config()
    if backend_name(config) == "memory":
        try:
            result = materialize_for_prompt(config, args.project_dir, task, args.user_id)
        except MemoryClientError as exc:
            print(f"No memory retrieved: {exc}")
            return 0
        rendered = str(result.get("rendered_text") or "").strip()
        if not rendered:
            print("No memory retrieved.")
            return 0
        print("## Chat2Skill Memory Prompt Snippet")
        print(rendered)
        print()
        print("## Retrieval Metadata")
        print(f"- materialization_id={result.get('materialization_id')}")
        print(f"- memory_bullets={len((result.get('memory') or {}).get('bullets_included') or [])}")
        print(f"- skills={', '.join((result.get('skills') or {}).get('skills_included') or [])}")
        return 0

    init_db()
    skills = load_skills(args.user_id, include_pending=False)
    retriever = SkillRetriever()
    retrieved = retriever.retrieve(task, skills, top_k=args.top_k, active_only=True)

    if not retrieved:
        print("No relevant Chat2Skill skills found.")
        return 0

    record_skill_usage(args.user_id, [item.skill.name for item in retrieved])

    print("## Chat2Skill Prompt Snippet")
    print(retriever.format_for_prompt(retrieved))
    print()
    print("## Skill Files")
    for item in retrieved:
        path = DATA_HOME / "skills" / args.user_id / item.skill.name / "SKILL.md"
        print(f"- {item.skill.name} score={item.score:.3f} path={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
