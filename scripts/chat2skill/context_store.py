"""Local project memory context store for unified memory and skills."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import storage
from .config import CONTEXTS_DIR
from .hookio import project_slug


def load_context(project_dir: str, user_id: str) -> dict[str, Any]:
    key = context_key(project_dir)
    stored = storage.load_project_memory_context(user_id, key)
    if stored is not None:
        stored["project_dir"] = str(Path(project_dir or stored.get("project_dir") or ".").expanduser())
        stored["user_id"] = user_id
        return _normalize_context(stored, project_dir, user_id)

    path = context_path(project_dir, user_id)
    if not path.exists():
        return _empty_context(project_dir, user_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_context(project_dir, user_id)
    if not isinstance(data, dict):
        return _empty_context(project_dir, user_id)
    context = _normalize_context(data, project_dir, user_id)
    save_context(project_dir, user_id, context)
    return context


def _normalize_context(data: dict[str, Any], project_dir: str, user_id: str) -> dict[str, Any]:
    data.setdefault("version", 1)
    data.setdefault("project_dir", str(Path(project_dir or ".").expanduser()))
    data.setdefault("user_id", user_id)
    data.setdefault("core_memory", "")
    data.setdefault("memories", [])
    data.setdefault("schemas", [])
    data.setdefault("recent_raw_hashes", [])
    data["schemas"] = [_to_memory_schema(item) for item in data.get("schemas") or []]
    data["last_materialization"] = _to_memory_receipt(data.get("last_materialization"))
    data.setdefault("last_materialization", None)
    return data


def save_context(project_dir: str, user_id: str, context: dict[str, Any]) -> Path:
    payload = {
        "version": 1,
        "project_dir": str(Path(project_dir or ".").expanduser()),
        "user_id": user_id,
        "core_memory": context.get("core_memory", ""),
        "memories": [_to_memory_item(item) for item in context.get("memories") or []],
        "schemas": [_to_memory_schema(item) for item in context.get("schemas") or []],
        "recent_raw_hashes": context.get("recent_raw_hashes") or [],
        "last_materialization": _to_memory_receipt(context.get("last_materialization")),
    }
    clean = _sanitize_for_storage(payload)
    storage.save_project_memory_context(user_id, context_key(project_dir), clean)
    if clean.get("last_materialization"):
        storage.save_project_memory_materialization(user_id, context_key(project_dir), clean["last_materialization"])
    return storage.DB_PATH


def context_state(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "core_memory": context.get("core_memory", ""),
        "memories": [_to_memory_item(item) for item in context.get("memories") or []],
        "schemas": [_to_memory_schema(item) for item in context.get("schemas") or []],
        "recent_raw_hashes": context.get("recent_raw_hashes") or [],
        "last_materialization": _to_memory_receipt(context.get("last_materialization")),
    }


def apply_memory_result(context: dict[str, Any], memory: dict[str, Any]) -> dict[str, Any]:
    batch = memory.get("delta_batch") or {}
    operations = batch.get("operations") or []
    memories = {
        str(item.get("id")): dict(item)
        for item in context.get("memories") or []
        if item.get("id")
    }
    schemas = {
        str(item.get("id")): dict(item)
        for item in context.get("schemas") or []
        if item.get("id")
    }

    for op in operations:
        op_type = op.get("op_type")
        target_id = str(op.get("target_id") or "")
        if op_type == "add_memory" and target_id:
            previous = op.get("previous_state") or {}
            memories[target_id] = {
                "id": target_id,
                "content": op.get("content") or "",
                "memory_type": op.get("memory_type") or "fact",
                "section": op.get("section") or "general",
                "salience": op.get("confidence", 0.5),
                "confidence": op.get("confidence", 0.5),
                "embedding": previous.get("embedding") or [],
                "source_session": previous.get("source_session"),
                "source_agent": previous.get("source_agent"),
                "recall_count": 0,
                "hit_count": 0,
                "miss_count": 0,
                "is_active": True,
                "is_archived": False,
            }
        elif op_type == "update_memory" and target_id in memories:
            if op.get("content") is not None:
                memories[target_id]["content"] = op["content"]
            if op.get("section") is not None:
                memories[target_id]["section"] = op["section"]
            memory_type = op.get("memory_type")
            if memory_type is not None:
                memories[target_id]["memory_type"] = memory_type
            memories[target_id]["confidence"] = max(
                float(memories[target_id].get("confidence") or 0.0),
                float(op.get("confidence") or 0.0),
            )
        elif op_type == "remove_memory" and target_id in memories:
            memories[target_id]["is_active"] = False
        elif op_type == "merge_memories":
            keep_id = target_id or str((op.get("target_ids") or [""])[0])
            if keep_id in memories and op.get("content"):
                memories[keep_id]["content"] = op["content"]
            for remove_id in op.get("target_ids") or []:
                remove_id = str(remove_id)
                if remove_id != keep_id and remove_id in memories:
                    memories[remove_id]["is_active"] = False
        elif op_type == "add_schema" and target_id:
            previous = op.get("previous_state") or {}
            schemas[target_id] = {
                "id": target_id,
                "name": op.get("content") or "schema",
                "description": op.get("reasoning") or "",
                "memory_ids": previous.get("memory_ids") or [],
            }
        elif op_type == "update_schema" and target_id in schemas:
            schemas[target_id]["description"] = op.get("content") or schemas[target_id].get("description", "")
        elif op_type == "update_core_memory" and op.get("content") is not None:
            context["core_memory"] = op["content"]
        elif op_type == "reconsolidate_memory" and target_id in memories:
            previous = op.get("previous_state") or {}
            memories[target_id]["recall_count"] = int(memories[target_id].get("recall_count") or 0) + int(previous.get("recall_delta") or 0)
            memories[target_id]["hit_count"] = int(memories[target_id].get("hit_count") or 0) + int(previous.get("hit_delta") or 0)
            memories[target_id]["miss_count"] = int(memories[target_id].get("miss_count") or 0) + int(previous.get("miss_delta") or 0)
            multiplier = float(previous.get("salience_multiplier") or 1.0)
            salience = float(memories[target_id].get("salience") or 0.5)
            memories[target_id]["salience"] = max(0.05, min(1.0, salience * multiplier))

    raw_hash = memory.get("raw_input_hash")
    recent = [str(item) for item in context.get("recent_raw_hashes") or []]
    if raw_hash and raw_hash not in recent:
        recent.append(str(raw_hash))
    context["recent_raw_hashes"] = recent[-100:]
    context["memories"] = [_to_memory_item(item) for item in memories.values()]
    context["schemas"] = [_to_memory_schema(item) for item in schemas.values()]
    if memory.get("core_memory_update"):
        context["core_memory"] = memory["core_memory_update"]
    return context


def save_materialization(
    context: dict[str, Any],
    result: dict[str, Any],
    query: str,
) -> dict[str, Any]:
    context["last_materialization"] = {
        "materialization_id": result.get("materialization_id"),
        "memories_included": (result.get("memory") or {}).get("memories_included") or [],
        "skills_included": (result.get("skills") or {}).get("skills_included") or [],
        "activities_included": (result.get("memory") or {}).get("activities_included") or [],
        "query": query,
        "rendered_prompt": result.get("rendered_text") or "",
        "token_count": result.get("token_count"),
    }
    return context


def context_path(project_dir: str, user_id: str) -> Path:
    slug = context_key(project_dir)
    safe_user = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in user_id)
    return CONTEXTS_DIR / safe_user / f"{slug}.json"


def context_key(project_dir: str) -> str:
    return project_slug(project_dir or "")


def _empty_context(project_dir: str, user_id: str) -> dict[str, Any]:
    return {
        "version": 1,
        "project_dir": str(Path(project_dir or ".").expanduser()),
        "user_id": user_id,
        "core_memory": "",
        "memories": [],
        "schemas": [],
        "recent_raw_hashes": [],
        "last_materialization": None,
    }


def _to_memory_item(item: dict[str, Any]) -> dict[str, Any]:
    memory = dict(item or {})
    return memory


def _to_memory_schema(item: dict[str, Any]) -> dict[str, Any]:
    schema = dict(item or {})
    return schema


def _to_memory_receipt(receipt: dict[str, Any] | None) -> dict[str, Any] | None:
    if not receipt:
        return None
    result = dict(receipt)
    return result


def _sanitize_for_storage(value: Any) -> Any:
    if isinstance(value, dict):
        return {_storage_key(key): _sanitize_for_storage(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_for_storage(item) for item in value]
    return value


def _storage_key(key: str) -> str:
    return {
    }.get(key, key)
