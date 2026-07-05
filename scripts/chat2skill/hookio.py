"""Hook input/output helpers shared by all hook entry points.

Works with Codex, Claude Code, and Cursor hook payloads: key names differ
between agents, so values are located by trying known aliases anywhere
in the input document.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .config import DATA_HOME, base_user_id
from .transcripts import find_latest_session

LOG_PATH = DATA_HOME / "hook-events.log"


def read_hook_input() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {"raw": raw}


def first_string(data: Any, keys: tuple[str, ...]) -> str:
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in data.values():
            found = first_string(value, keys)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = first_string(item, keys)
            if found:
                return found
    return ""


def project_dir_from_input(data: dict[str, Any]) -> str:
    import os

    env_value = (
        os.environ.get("CODEX_PROJECT_DIR")
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or os.environ.get("CURSOR_PROJECT_DIR")
        or os.environ.get("PWD")
        or ""
    )
    workspace_roots = data.get("workspace_roots")
    if isinstance(workspace_roots, list):
        for root in workspace_roots:
            if isinstance(root, str) and root.strip():
                return root.strip()
    value = first_string(
        data,
        (
            "cwd",
            "project_dir",
            "projectDir",
            "workspace_dir",
            "workspaceDir",
            "current_dir",
            "currentDir",
        ),
    )
    return value or env_value


def project_slug(project_dir: str) -> str:
    path = Path(project_dir).expanduser()
    name = path.name or "unknown-project"
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-").lower() or "unknown-project"
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def project_user_id(project_dir: str) -> str:
    return f"{base_user_id()}__project__{project_slug(project_dir)}"


def transcript_path_from_input(data: dict[str, Any]) -> Optional[Path]:
    value = first_string(
        data, ("transcript_path", "transcriptPath", "session_path", "sessionPath")
    )
    if value:
        path = Path(value).expanduser()
        if path.exists():
            return path
    return find_latest_session(project_dir_from_input(data))


def prompt_from_input(data: dict[str, Any]) -> str:
    return first_string(data, ("prompt", "user_prompt", "userPrompt", "message", "input", "text"))


def json_hook_output(additional_context: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": additional_context,
            }
        },
        sys.stdout,
        ensure_ascii=False,
    )


def log_event(event: str, **fields: Any) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {"timestamp": datetime.now().isoformat(), "event": event, **fields}
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass  # Logging must never break a hook.
