"""Local project memory context store for the unified Memory backend."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from .config import CONTEXTS_DIR
from .hookio import project_slug


def load_context(project_dir: str, user_id: str) -> dict[str, Any]:
    path = context_path(project_dir, user_id)
    if not path.exists():
        return _empty_context(project_dir, user_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_context(project_dir, user_id)
    if not isinstance(data, dict):
        return _empty_context(project_dir, user_id)
    data.setdefault("version", 1)
    data.setdefault("project_dir", str(Path(project_dir or ".").expanduser()))
    data.setdefault("user_id", user_id)
    data.setdefault("core_memory", "")
    data.setdefault("bullets", [])
    data.setdefault("schemas", [])
    data.setdefault("recent_raw_hashes", [])
    data.setdefault("last_materialization", None)
    return data


def save_context(project_dir: str, user_id: str, context: dict[str, Any]) -> Path:
    path = context_path(project_dir, user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "project_dir": str(Path(project_dir or ".").expanduser()),
        "user_id": user_id,
        "core_memory": context.get("core_memory", ""),
        "bullets": context.get("bullets") or [],
        "schemas": context.get("schemas") or [],
        "recent_raw_hashes": context.get("recent_raw_hashes") or [],
        "last_materialization": context.get("last_materialization"),
    }
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
    ) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)
    return path


def context_state(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "core_memory": context.get("core_memory", ""),
        "bullets": context.get("bullets") or [],
        "schemas": context.get("schemas") or [],
        "recent_raw_hashes": context.get("recent_raw_hashes") or [],
        "last_materialization": context.get("last_materialization"),
    }


def apply_memory_result(context: dict[str, Any], memory: dict[str, Any]) -> dict[str, Any]:
    batch = memory.get("delta_batch") or {}
    operations = batch.get("operations") or []
    bullets = {
        str(item.get("id")): dict(item)
        for item in context.get("bullets") or []
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
        if op_type == "add_bullet" and target_id:
            previous = op.get("previous_state") or {}
            bullets[target_id] = {
                "id": target_id,
                "content": op.get("content") or "",
                "bullet_type": op.get("bullet_type") or "fact",
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
        elif op_type == "update_bullet" and target_id in bullets:
            if op.get("content") is not None:
                bullets[target_id]["content"] = op["content"]
            if op.get("section") is not None:
                bullets[target_id]["section"] = op["section"]
            if op.get("bullet_type") is not None:
                bullets[target_id]["bullet_type"] = op["bullet_type"]
            bullets[target_id]["confidence"] = max(
                float(bullets[target_id].get("confidence") or 0.0),
                float(op.get("confidence") or 0.0),
            )
        elif op_type == "remove_bullet" and target_id in bullets:
            bullets[target_id]["is_active"] = False
        elif op_type == "merge_bullets":
            keep_id = target_id or str((op.get("target_ids") or [""])[0])
            if keep_id in bullets and op.get("content"):
                bullets[keep_id]["content"] = op["content"]
            for remove_id in op.get("target_ids") or []:
                remove_id = str(remove_id)
                if remove_id != keep_id and remove_id in bullets:
                    bullets[remove_id]["is_active"] = False
        elif op_type == "add_schema" and target_id:
            schemas[target_id] = {
                "id": target_id,
                "name": op.get("content") or "schema",
                "description": op.get("reasoning") or "",
                "bullet_ids": (op.get("previous_state") or {}).get("bullet_ids") or [],
            }
        elif op_type == "update_schema" and target_id in schemas:
            schemas[target_id]["description"] = op.get("content") or schemas[target_id].get("description", "")
        elif op_type == "update_core_memory" and op.get("content") is not None:
            context["core_memory"] = op["content"]
        elif op_type == "reconsolidate_bullet" and target_id in bullets:
            previous = op.get("previous_state") or {}
            bullets[target_id]["recall_count"] = int(bullets[target_id].get("recall_count") or 0) + int(previous.get("recall_delta") or 0)
            bullets[target_id]["hit_count"] = int(bullets[target_id].get("hit_count") or 0) + int(previous.get("hit_delta") or 0)
            bullets[target_id]["miss_count"] = int(bullets[target_id].get("miss_count") or 0) + int(previous.get("miss_delta") or 0)
            multiplier = float(previous.get("salience_multiplier") or 1.0)
            salience = float(bullets[target_id].get("salience") or 0.5)
            bullets[target_id]["salience"] = max(0.05, min(1.0, salience * multiplier))

    raw_hash = memory.get("raw_input_hash")
    recent = [str(item) for item in context.get("recent_raw_hashes") or []]
    if raw_hash and raw_hash not in recent:
        recent.append(str(raw_hash))
    context["recent_raw_hashes"] = recent[-100:]
    context["bullets"] = list(bullets.values())
    context["schemas"] = list(schemas.values())
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
        "bullets_included": (result.get("memory") or {}).get("bullets_included") or [],
        "query": query,
    }
    return context


def context_path(project_dir: str, user_id: str) -> Path:
    slug = project_slug(project_dir or "")
    safe_user = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in user_id)
    return CONTEXTS_DIR / safe_user / f"{slug}.json"


def _empty_context(project_dir: str, user_id: str) -> dict[str, Any]:
    return {
        "version": 1,
        "project_dir": str(Path(project_dir or ".").expanduser()),
        "user_id": user_id,
        "core_memory": "",
        "bullets": [],
        "schemas": [],
        "recent_raw_hashes": [],
        "last_materialization": None,
    }
