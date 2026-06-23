"""Generic Stop-hook completion review.

The guard reconciles the latest actionable user request with the final
assistant response. It is intentionally local and heuristic: no LLM call, no
project-specific rules, and no filesystem writes. It blocks only
high-confidence gaps where the response claims completion without evidence or
scope reconciliation.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .hookio import first_string, log_event, project_dir_from_input, transcript_path_from_input
from .transcripts import parse_transcript

DEFAULT_REVIEW_MODE = "strict"
REVIEW_MODES = {"strict", "warn-only", "off"}

ACTION_MARKERS = (
    "fix",
    "implement",
    "update",
    "modify",
    "change",
    "create",
    "add",
    "remove",
    "write",
    "build",
    "test",
    "review",
    "analyze",
    "research",
    "summarize",
    "report",
    "deploy",
    "修复",
    "实现",
    "修改",
    "改",
    "新增",
    "添加",
    "删除",
    "写",
    "生成",
    "创建",
    "检查",
    "核对",
    "分析",
    "报告",
    "总结",
    "部署",
    "测试",
    "继续",
)

COMPLETION_CLAIM_MARKERS = (
    "done",
    "completed",
    "complete",
    "fixed",
    "implemented",
    "updated",
    "created",
    "added",
    "removed",
    "verified",
    "已完成",
    "完成",
    "已修复",
    "修复了",
    "已实现",
    "实现了",
    "已更新",
    "更新了",
    "已添加",
    "已创建",
    "已验证",
)

EVIDENCE_MARKERS = (
    "verification",
    "verified",
    "test",
    "tests",
    "lint",
    "typecheck",
    "build",
    "screenshot",
    "source",
    "evidence",
    "diff",
    "checked",
    "validated",
    "not run",
    "not tested",
    "not verified",
    "residual risk",
    "验证",
    "已验证",
    "测试",
    "检查",
    "核对",
    "证据",
    "截图",
    "构建",
    "未运行",
    "未测试",
    "未验证",
    "剩余风险",
)

COMPLETENESS_MARKERS = (
    "complete",
    "comprehensive",
    "all",
    "every",
    "end-to-end",
    "full",
    "no missing",
    "coverage",
    "frontend",
    "backend",
    "database",
    "worker",
    "cli",
    "mcp",
    "gateway",
    "全面",
    "完整",
    "全部",
    "所有",
    "不要遗漏",
    "遗漏",
    "端到端",
    "调用链",
    "前端",
    "后端",
    "数据库",
    "测试",
    "覆盖",
)

RECONCILIATION_MARKERS = (
    "requirement",
    "requirements",
    "covered",
    "scope",
    "unchecked",
    "not covered",
    "remaining",
    "blocked",
    "waived",
    "done",
    "需求",
    "要求",
    "覆盖",
    "范围",
    "未检查",
    "未覆盖",
    "剩余",
    "阻塞",
    "完成项",
)

MULTI_ITEM_MARKERS = (
    "\n- ",
    "\n* ",
    "\n1.",
    "\n1 ",
    " and ",
    " also ",
    " as well as ",
    "以及",
    "同时",
    "并且",
    "还有",
    "另外",
)


@dataclass(frozen=True)
class CompletionReviewResult:
    blocked: bool
    reasons: tuple[str, ...] = ()
    prompt_preview: str = ""
    mode: str = ""


def evaluate_stop_payload(data: dict) -> CompletionReviewResult:
    """Evaluate one Stop hook payload."""
    mode = completion_review_mode()
    if mode == "off":
        return CompletionReviewResult(blocked=False, mode=mode)

    project_dir = project_dir_from_input(data)
    prompt = latest_user_prompt(data)
    assistant_message = assistant_message_from_input(data)
    result = evaluate_completion(
        prompt=prompt,
        assistant_message=assistant_message,
        mode=mode,
    )
    if result.blocked:
        log_event(
            "StopCompletionReview.blocked",
            project_dir=project_dir,
            mode=mode,
            reasons=list(result.reasons),
            prompt_preview=result.prompt_preview,
        )
    else:
        log_event("StopCompletionReview.passed", project_dir=project_dir, mode=mode)
    return result


def evaluate_completion(
    prompt: str,
    assistant_message: str,
    mode: str | None = None,
) -> CompletionReviewResult:
    """Return a block decision for missing final reconciliation evidence."""
    mode = normalize_mode(mode or completion_review_mode())
    prompt = clean_text(prompt)
    assistant_message = clean_text(assistant_message)

    if mode == "off" or not prompt or not assistant_message:
        return CompletionReviewResult(False, mode=mode)
    if not is_actionable_prompt(prompt):
        return CompletionReviewResult(False, mode=mode)
    if is_question_only(prompt):
        return CompletionReviewResult(False, mode=mode)

    has_completion_claim = contains_any(assistant_message, COMPLETION_CLAIM_MARKERS)
    has_evidence = contains_any(assistant_message, EVIDENCE_MARKERS)
    has_reconciliation = contains_any(assistant_message, RECONCILIATION_MARKERS)
    wants_completeness = contains_any(prompt, COMPLETENESS_MARKERS)
    is_multi_item = contains_any(prompt, MULTI_ITEM_MARKERS) or len(extract_requirement_items(prompt)) > 1

    reasons: list[str] = []
    if has_completion_claim and not has_evidence:
        reasons.append(
            "The final response claims completion but does not include verification evidence or an explicit unverified-test statement."
        )
    if wants_completeness and has_completion_claim and not (has_evidence and has_reconciliation):
        reasons.append(
            "The original request asks for broad or complete coverage, but the final response does not reconcile completed scope, evidence, and unchecked scope."
        )
    if is_multi_item and has_completion_claim and not has_reconciliation:
        reasons.append(
            "The original request contains multiple requirement items, but the final response does not map completion back to those items."
        )

    if not reasons or mode == "warn-only":
        return CompletionReviewResult(
            blocked=False,
            reasons=tuple(reasons),
            prompt_preview=preview(prompt),
            mode=mode,
        )
    return CompletionReviewResult(
        blocked=True,
        reasons=tuple(reasons),
        prompt_preview=preview(prompt),
        mode=mode,
    )


def completion_review_mode() -> str:
    return normalize_mode(os.environ.get("CHAT2SKILL_COMPLETION_REVIEW", DEFAULT_REVIEW_MODE))


def normalize_mode(raw: str) -> str:
    mode = (raw or "").strip().lower()
    aliases = {
        "1": "strict",
        "true": "strict",
        "block": "strict",
        "strict": "strict",
        "warn": "warn-only",
        "warning": "warn-only",
        "warn-only": "warn-only",
        "0": "off",
        "false": "off",
        "disabled": "off",
        "off": "off",
    }
    return aliases.get(mode, DEFAULT_REVIEW_MODE if mode not in REVIEW_MODES else mode)


def latest_user_prompt(data: dict) -> str:
    transcript_path = transcript_path_from_input(data)
    if transcript_path:
        for message in reversed(parse_transcript(transcript_path, clean=True)):
            if message.get("role") != "user":
                continue
            content = clean_text(message.get("content", ""))
            if content.startswith("<hook_prompt"):
                continue
            return content
    return first_string(data, ("prompt", "user_prompt", "userPrompt", "input"))


def assistant_message_from_input(data: dict) -> str:
    return first_string(
        data,
        (
            "last_assistant_message",
            "lastAssistantMessage",
            "assistant_message",
            "assistantMessage",
            "response",
            "output",
            "message",
            "text",
        ),
    )


def is_actionable_prompt(prompt: str) -> bool:
    return contains_any(prompt, ACTION_MARKERS)


def is_question_only(prompt: str) -> bool:
    text = prompt.strip()
    question_mark = text.endswith("?") or text.endswith("？")
    return question_mark and not contains_any(text, ACTION_MARKERS)


def extract_requirement_items(prompt: str) -> tuple[str, ...]:
    items: list[str] = []
    for line in prompt.splitlines():
        stripped = line.strip()
        if re.match(r"^([-*]|\d+[.)])\s+", stripped):
            items.append(stripped)
    if items:
        return tuple(items)
    parts = re.split(r"[。；;]\s*", prompt)
    return tuple(part.strip() for part in parts if contains_any(part, ACTION_MARKERS))[:8]


def contains_any(text: str, markers: Iterable[str]) -> bool:
    lower = text.lower()
    return any(marker.lower() in lower for marker in markers)


def clean_text(text: str) -> str:
    return (text or "").strip()


def preview(text: str, limit: int = 240) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:limit]


def stop_hook_output(result: CompletionReviewResult) -> str:
    if not result.blocked:
        return ""
    reason = (
        "Chat2Skill completion review found that the final response does not fully reconcile "
        "the original request with completion evidence.\n"
        "Missing checks:\n"
        + "\n".join(f"- {reason}" for reason in result.reasons)
        + "\nRequired next step: re-read the original request, compare it against the actual work, "
        "state completed items with evidence, state unchecked items, and continue the work when a gap remains."
    )
    return json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False)
