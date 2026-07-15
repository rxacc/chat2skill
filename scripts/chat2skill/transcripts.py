"""Transcript parsing for supported coding agents.

Handles two JSONL layouts:
- Codex rollouts: {"type": "response_item", "payload": {"role", "content"}}
- Claude Code transcripts: {"type": "user"|"assistant", "message": {"role", "content"}}
- Cursor agent transcripts: {"role": "user"|"assistant", "message": {"content": ...}}

Noise (agent instructions, environment banners, system reminders) is
stripped before anything is sent to the cloud.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

NOISE_MARKERS = (
    "# AGENTS.md",
    "<INSTRUCTIONS>",
    "<environment_context>",
    "<permissions instructions>",
    "You are Codex, a coding agent",
    "## Skills",
    "Filesystem sandboxing defines",
    "Codex desktop context",
    "<system-reminder>",
    "<command-name>",
    "<local-command-stdout>",
    "Caveat: The messages below were generated",
)


def parse_transcript(path: Path, clean: bool = True) -> List[dict]:
    """Return [{"role", "content"}, ...] from any supported transcript."""
    messages: List[dict] = []
    if not path.exists():
        return messages
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = _message_from_record(record)
            if message is None:
                continue
            role, content = message
            if clean:
                content = clean_message_content(role, content)
            if content:
                messages.append({"role": role, "content": content})
    return messages


def _message_from_record(record: dict) -> Optional[tuple[str, str]]:
    record_type = record.get("type")

    if record_type == "response_item":  # Codex rollout
        payload = record.get("payload", {})
        role = payload.get("role")
        if role not in ("user", "assistant"):
            return None
        return role, _flatten_content(payload.get("content", ""))

    if record_type in ("user", "assistant"):  # Claude Code transcript
        payload = record.get("message", {})
        role = payload.get("role") or record_type
        if role not in ("user", "assistant"):
            return None
        return role, _flatten_content(payload.get("content", ""))

    role = record.get("role")  # Cursor agent transcript
    if role in ("user", "assistant"):
        payload = record.get("message", {})
        if isinstance(payload, dict):
            return role, _flatten_content(payload.get("content", ""))
        return role, _flatten_content(payload)

    return None


def _flatten_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                texts.append(item["text"])
        return "\n".join(texts)
    return ""


def clean_message_content(role: str, content: str) -> str:
    """Remove agent/system noise before extraction."""
    if not isinstance(content, str):
        return ""
    text = content.strip()
    if not text:
        return ""
    if any(marker in text for marker in NOISE_MARKERS):
        return ""
    if role == "assistant" and text.startswith("Chunk ID:"):
        return ""
    return text


def find_latest_session(
    project_dir: str = "",
    *,
    session_id: str = "",
) -> Optional[Path]:
    """Find the newest transcript without crossing project boundaries."""
    candidates: List[Path] = []
    home = Path.home()
    cursor_root = home / ".cursor" / "projects"
    cursor_workspace = _cursor_workspace_transcripts(cursor_root, project_dir)
    codex_root = home / ".codex" / "sessions"
    claude_root = home / ".claude" / "projects"

    if project_dir:
        if cursor_workspace is not None:
            candidates.extend(_jsonl_files(cursor_workspace))
        if codex_root.exists():
            candidates.extend(
                path
                for path in _jsonl_files(codex_root)
                if _codex_session_matches_project(path, project_dir)
            )
        claude_workspace = _claude_workspace_transcripts(claude_root, project_dir)
        if claude_workspace is not None:
            candidates.extend(_jsonl_files(claude_workspace))
    else:
        for root in (codex_root, claude_root, cursor_root):
            candidates.extend(_jsonl_files(root))

    if session_id:
        candidates = [
            path for path in candidates if _session_id_matches(path, session_id)
        ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def transcript_matches_project(path: Path, project_dir: str) -> bool:
    """Validate known host transcript paths against the active project."""
    if not project_dir:
        return True
    home = Path.home()
    codex_root = home / ".codex" / "sessions"
    claude_root = home / ".claude" / "projects"
    cursor_root = home / ".cursor" / "projects"
    if _path_is_within(path, codex_root):
        return _codex_session_matches_project(path, project_dir)
    if _path_is_within(path, claude_root):
        workspace = _claude_workspace_transcripts(claude_root, project_dir)
        return workspace is not None and _path_is_within(path, workspace)
    if _path_is_within(path, cursor_root):
        workspace = _cursor_workspace_transcripts(cursor_root, project_dir)
        return workspace is not None and _path_is_within(path, workspace)
    return True


def _jsonl_files(root: Path) -> List[Path]:
    if not root.exists():
        return []
    return [path for path in root.rglob("*.jsonl") if path.is_file()]


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(root.expanduser().resolve())
        return True
    except (OSError, ValueError):
        return False


def _session_id_matches(path: Path, session_id: str) -> bool:
    value = session_id.strip()
    return bool(value) and (path.stem == value or path.stem.endswith(f"-{value}"))


def _codex_session_matches_project(path: Path, project_dir: str) -> bool:
    expected = _normalized_path(project_dir)
    if not expected:
        return False
    try:
        with path.open("r", encoding="utf-8") as transcript:
            for _ in range(20):
                line = transcript.readline()
                if not line:
                    break
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") != "session_meta":
                    continue
                cwd = record.get("payload", {}).get("cwd", "")
                return _normalized_path(cwd) == expected
    except OSError:
        return False
    return False


def _normalized_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        return str(Path(value).expanduser().resolve())
    except OSError:
        return str(Path(value).expanduser())


def _claude_workspace_transcripts(
    claude_root: Path,
    project_dir: str,
) -> Optional[Path]:
    if not project_dir or not claude_root.exists():
        return None
    raw = _normalized_path(project_dir)
    transcript_dir = claude_root / raw.replace("/", "-")
    return transcript_dir if transcript_dir.exists() else None


def _cursor_workspace_transcripts(cursor_root: Path, project_dir: str) -> Optional[Path]:
    if not project_dir or not cursor_root.exists():
        return None
    try:
        raw = str(Path(project_dir).expanduser().resolve())
    except OSError:
        raw = str(Path(project_dir).expanduser())
    slug = raw.strip("/").replace("/", "-")
    transcript_dir = cursor_root / slug / "agent-transcripts"
    return transcript_dir if transcript_dir.exists() else None
