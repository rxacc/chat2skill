#!/usr/bin/env python3
"""Stop hook: reconcile the original request with the final response."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chat2skill.completion_review import evaluate_stop_payload, stop_hook_output
from chat2skill.hookio import read_hook_input


def main() -> int:
    data = read_hook_input()
    output = stop_hook_output(evaluate_stop_payload(data))
    if output:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
