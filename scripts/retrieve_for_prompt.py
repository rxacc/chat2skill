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

from chat2skill.config import base_user_id, load_config
from chat2skill.initializer import ensure_user_home
from chat2skill.memory_client import MemoryClientError, materialize_for_prompt


def main() -> int:
    ensure_user_home(create_db=True)
    parser = argparse.ArgumentParser(description="Retrieve learned Chat2Skill skills.")
    parser.add_argument("task", nargs="+", help="Current user task text.")
    parser.add_argument("--user-id", default=base_user_id())
    parser.add_argument("--project-dir", default=os.getcwd())
    args = parser.parse_args()

    task = " ".join(args.task)
    config = load_config()
    try:
        result = materialize_for_prompt(config, args.project_dir, task, args.user_id)
    except MemoryClientError as exc:
        print(f"No memory retrieved: {exc}")
        return 0
    rendered = str(result.get("rendered_text") or "").strip()
    if not rendered:
        print("No memory retrieved.")
        return 0
    print("## Chat2Skill Memory and Skills Prompt Snippet")
    print(rendered)
    print()
    print("## Retrieval Metadata")
    print(f"- materialization_id={result.get('materialization_id')}")
    print(f"- memories={len((result.get('memory') or {}).get('memories_included') or [])}")
    print(f"- skills={', '.join((result.get('skills') or {}).get('skills_included') or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
