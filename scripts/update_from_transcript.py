#!/usr/bin/env python3
"""CLI: run extraction over transcript files (batch / manual)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chat2skill import runner
from chat2skill.config import base_user_id, load_config
from chat2skill.transcripts import find_latest_session


def main() -> int:
    parser = argparse.ArgumentParser(description="Update Chat2Skill from transcripts.")
    parser.add_argument("--input", action="append", help="Transcript JSONL file (repeatable).")
    parser.add_argument("--latest", action="store_true", help="Process the newest session file.")
    parser.add_argument("--user-id", default=base_user_id())
    parser.add_argument("--no-clean", action="store_true", help="Keep system/noise blocks.")
    parser.add_argument("--project-dir", default="", help="Project directory for Memory context mapping.")
    args = parser.parse_args()

    if args.input:
        sessions = [Path(p).expanduser() for p in args.input]
    elif args.latest:
        latest = find_latest_session()
        if not latest:
            print("No session files found.", file=sys.stderr)
            return 2
        sessions = [latest]
    else:
        parser.error("provide --input or --latest")
        return 2

    config = load_config()
    for index, session in enumerate(sessions, 1):
        print(f"[{index}/{len(sessions)}] {session}")
        result = runner.run_extraction(
            session,
            args.user_id,
            config,
            clean=not args.no_clean,
            project_dir=args.project_dir,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("status") == "saved":
            summary = runner.rebuild_project_summary(args.user_id, config)
            if summary:
                print(f"Project summary updated: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
