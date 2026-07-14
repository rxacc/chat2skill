#!/usr/bin/env python3
"""Stop hook: force learned hard wording constraints before the turn ends."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chat2skill.hookio import log_event, read_hook_input
from chat2skill.initializer import ensure_user_home
from chat2skill.response_guard import (
    blocking_stop_hook_supported,
    evaluate_stop_payload,
    stop_hook_output,
)


def main() -> int:
    ensure_user_home(create_db=True)
    data = read_hook_input()
    result = evaluate_stop_payload(data)
    if result.blocked:
        if blocking_stop_hook_supported(os.environ):
            log_event(
                "StopResponseGuard.blocked",
                user_id=result.user_id,
                matched_terms=list(result.terms),
                mode=result.mode,
            )
            sys.stdout.write(stop_hook_output(result))
        else:
            log_event(
                "StopResponseGuard.suppressed",
                user_id=result.user_id,
                matched_terms=list(result.terms),
                mode=result.mode,
                reason="codex_blocking_stop_hook_unsupported",
            )
    elif result.suppressed:
        log_event(
            "StopResponseGuard.suppressed",
            user_id=result.user_id,
            matched_terms=list(result.terms),
            mode=result.mode,
            reason=result.suppression_reason,
        )
    else:
        log_event("StopResponseGuard.passed", user_id=result.user_id, mode=result.mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
