#!/usr/bin/env python3
"""Cross-agent Chat2Skill hook launcher."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


HOOK_SCRIPTS = {
    "user-prompt-submit": "hook_user_prompt_submit.py",
    "stop": "hook_stop.py",
    "stop-response-guard": "hook_stop_response_guard.py",
}


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: chat2skill_hook.py <hook-name>", file=sys.stderr)
        return 2

    hook_name = sys.argv[1]
    script_name = HOOK_SCRIPTS.get(hook_name)
    if not script_name:
        valid = ", ".join(sorted(HOOK_SCRIPTS))
        print(f"unknown hook: {hook_name}; valid hooks: {valid}", file=sys.stderr)
        return 2

    scripts_dir = Path(__file__).resolve().parent
    target = scripts_dir / script_name
    sys.path.insert(0, str(scripts_dir))
    sys.argv = [str(target), *sys.argv[2:]]
    runpy.run_path(str(target), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
