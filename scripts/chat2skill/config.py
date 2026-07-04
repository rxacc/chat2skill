"""User-level configuration.

Everything lives under the data home (default ~/.chat2skill, overridable
with CHAT2SKILL_HOME). The LLM api key belongs to the user (BYOK); it is
sent to the Chat2Skill cloud only to run this user's own extraction calls.
"""

from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
from typing import Optional

DATA_HOME = Path(os.environ.get("CHAT2SKILL_HOME") or Path.home() / ".chat2skill")
CONFIG_PATH = DATA_HOME / "config.json"
CONTEXTS_DIR = DATA_HOME / "contexts"

DEFAULT_API_URL = "https://api.chat2skill.com"
DEFAULT_LOCAL_EMBEDDING_MODEL = "Snowflake/snowflake-arctic-embed-xs"
DEFAULT_LOCAL_EMBEDDING_DIMENSIONS = 384
DEFAULT_REMOTE_EMBEDDING_MODEL = "text-embedding-3-small"


def load_config() -> dict:
    config: dict = {}
    if CONFIG_PATH.exists():
        try:
            config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            config = {}

    # Environment variables override the file.
    config.setdefault("api_url", DEFAULT_API_URL)
    if os.environ.get("CHAT2SKILL_API_URL"):
        config["api_url"] = os.environ["CHAT2SKILL_API_URL"]

    memory = dict(config.get("memory") or {})
    memory.setdefault("target_model", "claude")
    memory.setdefault("token_budget", 4000)
    memory.setdefault("memory_ratio", 0.6)
    memory.setdefault("skill_top_k", 6)
    memory.setdefault("agent_id", "chat2skill")
    if os.environ.get("CHAT2SKILL_MEMORY_TARGET_MODEL"):
        memory["target_model"] = os.environ["CHAT2SKILL_MEMORY_TARGET_MODEL"]
    if os.environ.get("CHAT2SKILL_MEMORY_TOKEN_BUDGET"):
        try:
            memory["token_budget"] = int(os.environ["CHAT2SKILL_MEMORY_TOKEN_BUDGET"])
        except ValueError:
            pass
    if os.environ.get("CHAT2SKILL_MEMORY_MEMORY_RATIO"):
        try:
            memory["memory_ratio"] = float(os.environ["CHAT2SKILL_MEMORY_MEMORY_RATIO"])
        except ValueError:
            pass
    if os.environ.get("CHAT2SKILL_MEMORY_SKILL_TOP_K"):
        try:
            memory["skill_top_k"] = int(os.environ["CHAT2SKILL_MEMORY_SKILL_TOP_K"])
        except ValueError:
            pass
    config["memory"] = memory

    llm = dict(config.get("llm") or {})
    if os.environ.get("OPENAI_API_KEY") and not llm.get("api_key"):
        llm["api_key"] = os.environ["OPENAI_API_KEY"]
    if os.environ.get("OPENAI_BASE_URL") and not llm.get("base_url"):
        llm["base_url"] = os.environ["OPENAI_BASE_URL"]
    if os.environ.get("CHAT2SKILL_MODEL"):
        llm["model"] = os.environ["CHAT2SKILL_MODEL"]
    llm.setdefault("model", "gpt-4.1")
    config["llm"] = llm

    embedding = dict(config.get("embedding") or {})
    if os.environ.get("CHAT2SKILL_EMBEDDING_PROVIDER"):
        embedding["provider"] = os.environ["CHAT2SKILL_EMBEDDING_PROVIDER"]
    if os.environ.get("CHAT2SKILL_EMBEDDING_API_KEY"):
        embedding["api_key"] = os.environ["CHAT2SKILL_EMBEDDING_API_KEY"]
    if os.environ.get("CHAT2SKILL_EMBEDDING_BASE_URL"):
        embedding["base_url"] = os.environ["CHAT2SKILL_EMBEDDING_BASE_URL"]
    if os.environ.get("CHAT2SKILL_EMBEDDING_MODEL"):
        embedding["model"] = os.environ["CHAT2SKILL_EMBEDDING_MODEL"]
    if os.environ.get("CHAT2SKILL_EMBEDDING_DIMENSIONS"):
        try:
            embedding["dimensions"] = int(os.environ["CHAT2SKILL_EMBEDDING_DIMENSIONS"])
        except ValueError:
            pass
    if not embedding.get("api_key") and os.environ.get("OPENAI_API_KEY"):
        embedding["api_key"] = os.environ["OPENAI_API_KEY"]
    if not embedding.get("base_url") and os.environ.get("OPENAI_BASE_URL"):
        embedding["base_url"] = os.environ["OPENAI_BASE_URL"]
    if embedding.get("provider") == "local_transformers":
        embedding.setdefault("model", DEFAULT_LOCAL_EMBEDDING_MODEL)
        embedding.setdefault("dimensions", DEFAULT_LOCAL_EMBEDDING_DIMENSIONS)
    else:
        embedding.setdefault("model", llm.get("embedding_model") or DEFAULT_REMOTE_EMBEDDING_MODEL)
    config["embedding"] = embedding

    if os.environ.get("CHAT2SKILL_USER_ID"):
        config["user_id"] = os.environ["CHAT2SKILL_USER_ID"]
    return config


def llm_payload(config: dict) -> Optional[dict]:
    """LLM block for API requests, or None to use server-side heuristics."""
    llm = config.get("llm") or {}
    if not llm.get("api_key"):
        return None
    payload = {
        "api_key": llm["api_key"],
        "base_url": llm.get("base_url"),
        "model": llm.get("model", "gpt-4.1"),
        "embedding_model": llm.get("embedding_model"),
    }
    embedding = embedding_payload(config)
    if embedding:
        payload["embedding_api_key"] = embedding["api_key"]
        payload["embedding_base_url"] = embedding.get("base_url")
        payload["embedding_model"] = embedding.get("model")
    return payload


def embedding_payload(config: dict) -> Optional[dict]:
    embedding = config.get("embedding") or {}
    if embedding.get("provider") == "local_transformers":
        return None
    if not embedding.get("api_key"):
        return None
    return {
        "api_key": embedding["api_key"],
        "base_url": embedding.get("base_url"),
        "model": embedding.get("model") or DEFAULT_REMOTE_EMBEDDING_MODEL,
    }


def embedding_config(config: dict) -> dict:
    embedding = dict(config.get("embedding") or {})
    if embedding.get("provider") == "local_transformers":
        embedding.setdefault("model", DEFAULT_LOCAL_EMBEDDING_MODEL)
        embedding.setdefault("dimensions", DEFAULT_LOCAL_EMBEDDING_DIMENSIONS)
    return embedding


def base_user_id(config: Optional[dict] = None) -> str:
    config = config or load_config()
    return config.get("user_id") or _safe_username() or "default"


def _safe_username() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return ""
