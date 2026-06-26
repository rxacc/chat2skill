"""Orchestrates one extraction round trip against the cloud API.

Local responsibilities: parse the transcript, gather context (skills,
profile, history), call the API, persist the returned records, refresh
the project-level summary skill.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from . import api_client, storage
from .config import backend_name, llm_payload
from .memory_client import commit_transcript
from .maintenance import SkillMaintainer
from .models import Skill, UserModel
from .transcripts import parse_transcript

HISTORY_LIMIT = 20
HISTORY_MESSAGES_PER_CONV = 30
HISTORY_CHARS_PER_MESSAGE = 2000
EXISTING_SKILLS_LIMIT = 30
PROJECT_SUMMARY_FILE = "PROJECT_SKILL.md"
PROJECT_SUMMARY_NAME = "project-chat2skill-summary"


def run_extraction(
    session_file: Path,
    user_id: str,
    config: dict,
    clean: bool = True,
    project_dir: str = "",
) -> dict:
    """Extract from one transcript. Returns a summary dict for logging."""
    if backend_name(config) == "memory":
        return commit_transcript(
            session_file=session_file,
            user_id=user_id,
            config=config,
            project_dir=project_dir,
            clean=clean,
        )

    messages = parse_transcript(session_file, clean=clean)
    if len(messages) < 2:
        return {"status": "skipped", "reason": "too_few_messages"}

    storage.init_db()
    session_id = session_file.stem
    storage.save_conversation(session_id, user_id, messages)

    existing = storage.load_skills(user_id)
    profile = storage.load_user_profile(user_id)
    history = _history_samples(user_id, exclude_session_id=session_id)

    payload = {
        "session_id": session_id,
        "user_id": user_id,
        "messages": messages,
        "existing_skills": [s.to_dict() for s in existing[:EXISTING_SKILLS_LIMIT]],
        "user_profile": profile.to_dict(),
        "history_samples": history,
        "llm": llm_payload(config),
    }
    response = api_client.extract(config["api_url"], payload)

    storage.save_user_profile(UserModel.from_dict(response["updated_profile"]))

    skill_data = response.get("skill")
    if not skill_data:
        return {
            "status": "no_skill",
            "reason": response.get("reason"),
            "llm_used": response.get("llm_used"),
        }

    skill = Skill.from_dict(skill_data)
    if skill.status == "rejected":
        return {
            "status": "rejected",
            "skill": skill.name,
            "replay": response.get("replay"),
            "llm_used": response.get("llm_used"),
        }

    # Embedding already computed server-side; no embedding client needed.
    storage.save_skill(skill, user_id=user_id)
    return {
        "status": "saved",
        "skill": skill.name,
        "skill_status": skill.status,
        "replay": response.get("replay"),
        "llm_used": response.get("llm_used"),
    }


def rebuild_project_summary(
    user_id: str,
    config: dict,
    recent_messages: Optional[List[dict]] = None,
) -> Optional[Path]:
    """Refresh PROJECT_SKILL.md from the user's active skills."""
    storage.init_db()
    skills = [
        skill
        for skill in storage.load_skills(user_id, include_pending=False)
        if skill.status == "active" and skill.name != PROJECT_SUMMARY_NAME
    ]
    if not skills:
        return None

    payload = {
        "user_id": user_id,
        "skills": [s.to_dict() for s in skills],
        "recent_messages": recent_messages or [],
        "existing_language": _existing_summary_language(user_id),
        "llm": llm_payload(config),
    }
    response = api_client.project_skill(config["api_url"], payload)

    out_dir = storage.SKILL_DIR / user_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / PROJECT_SUMMARY_FILE
    out_path.write_text(response["content"], encoding="utf-8")
    return out_path


def run_maintenance(user_id: str) -> dict:
    """Score, archive, and dedupe the local skill bank for one user."""
    storage.init_db()
    skills = storage.load_skills(user_id)
    usage = storage.load_usage_counts(user_id)
    report = SkillMaintainer().maintain(skills, usage)

    for loser, winner, similarity in report.merged:
        storage.absorb_skill_sources(winner, loser, user_id)
        storage.set_skill_status(loser, user_id, "archived", note=f"deduped_into:{winner}")

    for name in report.pruned:
        score = report.scores[name]
        storage.set_skill_status(name, user_id, "archived", note=f"pruned:score={score.total:.2f}")

    return {
        "kept": len(report.kept),
        "archived": report.pruned,
        "merged": [(loser, winner) for loser, winner, _ in report.merged],
    }


def _history_samples(user_id: str, exclude_session_id: str) -> List[dict]:
    samples = []
    for conv in storage.load_conversations(user_id, limit=HISTORY_LIMIT):
        if conv["session_id"] == exclude_session_id:
            continue
        trimmed = [
            {
                "role": m.get("role", "user"),
                "content": str(m.get("content", ""))[:HISTORY_CHARS_PER_MESSAGE],
            }
            for m in conv["messages"][-HISTORY_MESSAGES_PER_CONV:]
        ]
        samples.append({"session_id": conv["session_id"], "messages": trimmed})
    return samples


def _existing_summary_language(user_id: str) -> Optional[str]:
    import re

    path = storage.SKILL_DIR / user_id / PROJECT_SUMMARY_FILE
    if not path.exists():
        return None
    try:
        head = path.read_text(encoding="utf-8")[:2000]
    except OSError:
        return None
    match = re.search(r"^language:\s*([A-Za-z-]+)\s*$", head, flags=re.MULTILINE)
    return match.group(1) if match else None
