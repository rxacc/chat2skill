#!/usr/bin/env python3
"""Stop hook: enqueue extraction without blocking the agent."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chat2skill.config import DATA_HOME
from chat2skill.initializer import ensure_user_home
from chat2skill.hookio import (
    log_event,
    project_dir_from_input,
    project_user_id,
    read_hook_input,
    transcript_path_from_input,
)

QUEUE_PATH = DATA_HOME / "stop-queue.jsonl"


def main() -> int:
    ensure_user_home(create_db=True)
    data = read_hook_input()
    session_file = transcript_path_from_input(data)
    if not session_file:
        log_event("Stop.skipped", reason="no_session_file")
        return 0

    project_dir = project_dir_from_input(data)
    scoped_user_id = project_user_id(project_dir)
    job = {
        "queued_at": time.time(),
        "project_dir": project_dir,
        "user_id": scoped_user_id,
        "session_file": str(session_file),
    }

    DATA_HOME.mkdir(parents=True, exist_ok=True)
    with QUEUE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(job, ensure_ascii=False) + "\n")

    worker = Path(__file__).resolve().parent / "process_stop_queue.py"
    with (DATA_HOME / "stop-worker.spawn.log").open("a", encoding="utf-8") as log:
        subprocess.Popen(
            [sys.executable, str(worker)],
            cwd=str(worker.parent),
            env=os.environ.copy(),
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    log_event(
        "Stop.queued",
        project_dir=project_dir,
        user_id=scoped_user_id,
        session_file=str(session_file),
        queue=str(QUEUE_PATH),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
