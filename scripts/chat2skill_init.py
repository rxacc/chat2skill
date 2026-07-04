#!/usr/bin/env python3
"""Initialize the local Chat2Skill data directory."""

from __future__ import annotations

from chat2skill.initializer import ensure_user_home


def main() -> int:
    result = ensure_user_home(create_db=True)
    print(f"Chat2Skill data home: {result['data_home']}")
    print(f"Config: {result['config_path']}")
    print(f"Database: {result['db_path']}")
    print(f"Skills: {result['skill_dir']}")
    if result["created_config"]:
        print("Created config.json. Edit it to set your LLM api key.")
    else:
        print("Config already exists.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
