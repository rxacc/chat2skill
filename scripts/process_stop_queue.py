#!/usr/bin/env python3
"""Background worker: drain the Stop queue and call the Chat2Skill cloud."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chat2skill import runner
from chat2skill.api_client import ApiError
from chat2skill.config import DATA_HOME, load_config
from chat2skill.initializer import ensure_user_home
from chat2skill.memory_client import MemoryClientError
from chat2skill.hookio import log_event
from chat2skill.transcripts import parse_transcript

QUEUE_PATH = DATA_HOME / "stop-queue.jsonl"
LOCK_PATH = DATA_HOME / "stop-worker.lock"
MAX_BATCH_LOOPS = 3
PROJECT_SKILL_REBUILD_STATUSES = {"saved", "memory_saved"}


def main() -> int:
    ensure_user_home(create_db=True)
    if not acquire_lock():
        log_event("StopWorker.skipped", reason="worker_already_running")
        return 0
    try:
        config = load_config()
        for _ in range(MAX_BATCH_LOOPS):
            jobs = read_and_clear_queue()
            if not jobs:
                break
            for job in coalesce_jobs(jobs):
                process_job(job, config)
            time.sleep(0.2)
        return 0
    finally:
        release_lock()


def acquire_lock() -> bool:
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if lock_is_stale():
            try:
                LOCK_PATH.unlink()
            except FileNotFoundError:
                pass
            return acquire_lock()
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return True


def lock_is_stale() -> bool:
    try:
        pid = int(LOCK_PATH.read_text(encoding="utf-8").strip())
    except Exception:
        return True
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True
    except PermissionError:
        return False


def release_lock() -> None:
    try:
        if LOCK_PATH.read_text(encoding="utf-8").strip() == str(os.getpid()):
            LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


def read_and_clear_queue() -> list[dict[str, Any]]:
    if not QUEUE_PATH.exists():
        return []
    try:
        raw = QUEUE_PATH.read_text(encoding="utf-8")
        QUEUE_PATH.write_text("", encoding="utf-8")
    except FileNotFoundError:
        return []
    jobs = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            job = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(job, dict):
            jobs.append(job)
    return jobs


def coalesce_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_by_project: dict[str, dict[str, Any]] = {}
    for job in jobs:
        key = str(job.get("user_id") or job.get("project_dir") or "")
        if not key:
            continue
        session_file = Path(str(job.get("session_file", ""))).expanduser()
        if not session_file.exists():
            continue
        previous = latest_by_project.get(key)
        if previous is None:
            latest_by_project[key] = job
            continue
        previous_file = Path(str(previous.get("session_file", ""))).expanduser()
        if session_file.stat().st_mtime >= previous_file.stat().st_mtime:
            latest_by_project[key] = job
    return list(latest_by_project.values())


def process_job(job: dict[str, Any], config: dict) -> None:
    user_id = str(job["user_id"])
    session_file = Path(str(job["session_file"])).expanduser()
    project_dir = str(job.get("project_dir") or "")

    log_event("StopWorker.job_start", user_id=user_id, session_file=str(session_file))
    try:
        result = runner.run_extraction(
            session_file, user_id, config, project_dir=project_dir
        )
    except (ApiError, MemoryClientError) as exc:
        log_event("StopWorker.extract_failed", user_id=user_id, error=str(exc))
        return
    except Exception as exc:  # noqa: BLE001 — worker must survive any job
        log_event(
            "StopWorker.extract_failed",
            user_id=user_id,
            error=f"{type(exc).__name__}: {str(exc)[:240]}",
        )
        return

    project_skill_path = None
    if result.get("status") == "saved":
        try:
            maintenance = runner.run_maintenance(user_id)
            if maintenance["archived"] or maintenance["merged"]:
                log_event("StopWorker.maintenance", user_id=user_id, **maintenance)
        except Exception as exc:  # noqa: BLE001
            log_event(
                "StopWorker.maintenance_failed",
                user_id=user_id,
                error=f"{type(exc).__name__}: {str(exc)[:240]}",
            )
    if result.get("status") in PROJECT_SKILL_REBUILD_STATUSES:
        try:
            recent = parse_transcript(session_file)[-30:]
            project_skill_path = runner.rebuild_project_skill(user_id, config, recent)
        except ApiError as exc:
            log_event("StopWorker.project_skill_failed", user_id=user_id, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            log_event(
                "StopWorker.project_skill_failed",
                user_id=user_id,
                error=f"{type(exc).__name__}: {str(exc)[:240]}",
            )

    log_event(
        "StopWorker.job_done",
        user_id=user_id,
        session_file=str(session_file),
        result=result,
        project_skill_path=str(project_skill_path) if project_skill_path else None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
