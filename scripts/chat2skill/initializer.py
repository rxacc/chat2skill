"""Cross-platform local data initialization for Chat2Skill."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import config, storage


def ensure_user_home(*, create_db: bool = True) -> dict:
    """Create the local Chat2Skill home, default config, and SQLite DB."""
    config.DATA_HOME.mkdir(parents=True, exist_ok=True)
    config.CONTEXTS_DIR.mkdir(parents=True, exist_ok=True)
    created_config = _ensure_config()
    if create_db:
        storage.init_db()
    return {
        "data_home": str(config.DATA_HOME),
        "config_path": str(config.CONFIG_PATH),
        "db_path": str(storage.DB_PATH),
        "skill_dir": str(storage.SKILL_DIR),
        "created_config": created_config,
    }


def _ensure_config() -> bool:
    if config.CONFIG_PATH.exists():
        return False
    example = _plugin_root() / "config.example.json"
    config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if example.exists():
        shutil.copyfile(example, config.CONFIG_PATH)
    else:
        config.CONFIG_PATH.write_text(
            json.dumps(_default_config(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return True


def _plugin_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_config() -> dict:
    return {
        "memory": {
            "target_model": "generic",
            "token_budget": 4000,
            "memory_ratio": 0.6,
            "skill_top_k": 6,
            "prompt_memory_top_k": 12,
            "learn_memory_top_k": 40,
            "learn_skill_top_k": 20,
        },
        "api_url": config.DEFAULT_API_URL,
        "llm": {
            "api_key": "",
            "provider": "openai",
            "base_url": None,
            "model": "gpt-4.1",
        },
        "embedding": {
            "provider": "local_transformers",
            "model": config.DEFAULT_LOCAL_EMBEDDING_MODEL,
            "dimensions": config.DEFAULT_LOCAL_EMBEDDING_DIMENSIONS,
        },
    }
