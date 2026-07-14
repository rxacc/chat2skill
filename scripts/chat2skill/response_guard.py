"""Stop-hook response guard for learned project constraints.

The extraction pipeline learns rules from conversations. This module keeps the
runtime guard generic: it reads guard terms from local skill files and checks
the final assistant message without embedding project-specific policy words.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, List, Mapping, Sequence
import re

from . import storage
from .hookio import first_string, project_dir_from_input, project_user_id
from .runner import PROJECT_SKILL_FILE


FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
DEFAULT_GUARD_MODE = "adaptive"
GUARD_MODES = {"adaptive", "block-once", "strict", "warn-only", "off"}
GUARD_STATE_PATH = storage.DATA_HOME / "response-guard-state.json"
GUARD_STATE_TTL_SECONDS = 24 * 60 * 60
GUARD_STATE_MAX_ENTRIES = 512
ADAPTIVE_MAX_COOLDOWN = 8
EVIDENCE_BASED_MODE = "evidence_based_terms"


@dataclass(frozen=True)
class GuardResult:
    blocked: bool
    terms: tuple[str, ...] = ()
    reason: str = ""
    user_id: str = ""
    suppressed: bool = False
    mode: str = ""
    suppression_reason: str = ""


@dataclass(frozen=True)
class GuardPolicy:
    mode: str
    forbidden_terms: tuple[str, ...]
    requires_evidence: bool = False
    allow_evidence_gap_disclosure: bool = False
    strict_terms: tuple[str, ...] = ()
    evidence_markers: tuple[str, ...] = ()
    gap_markers: tuple[str, ...] = ()


def evaluate_stop_payload(data: dict) -> GuardResult:
    """Evaluate one Stop/SubagentStop hook payload."""
    project_dir = project_dir_from_input(data)
    user_id = project_user_id(project_dir)
    mode = response_guard_mode()
    if mode == "off":
        return GuardResult(blocked=False, user_id=user_id, mode=mode)

    message = assistant_message_from_input(data)
    if not message.strip():
        return GuardResult(blocked=False, user_id=user_id, mode=mode)

    sources = load_guard_sources(user_id)
    result = evaluate_message(message, sources=sources, user_id=user_id)
    return apply_response_guard_mode(result, mode=mode)


def evaluate_message(
    message: str,
    sources: Sequence[str],
    user_id: str = "",
) -> GuardResult:
    """Return a block decision when the message violates local guard terms."""
    policies = extract_guard_policies(sources)
    if not policies:
        return GuardResult(blocked=False, user_id=user_id)

    hits = find_guard_violations(message, policies)
    if not hits:
        return GuardResult(blocked=False, user_id=user_id)

    return GuardResult(
        blocked=True,
        terms=tuple(hits),
        reason=(
            "Chat2Skill guard matched unsupported hedging terms in active project skill rules. "
            "Rewrite the previous response once to satisfy those rules. "
            "Use definitive wording for verified facts. When evidence is missing, state the missing evidence "
            "and the next validation step instead of guessing. Keep facts, evidence, file paths, and conclusion."
        ),
        user_id=user_id,
    )


def response_guard_mode() -> str:
    """Return the configured Stop response guard mode."""
    raw = os.environ.get("CHAT2SKILL_RESPONSE_GUARD", DEFAULT_GUARD_MODE)
    mode = raw.strip().lower()
    aliases = {
        "1": "strict",
        "true": "strict",
        "block": "strict",
        "adaptive": "adaptive",
        "once": "block-once",
        "block_once": "block-once",
        "warn": "warn-only",
        "warning": "warn-only",
        "0": "off",
        "false": "off",
        "disabled": "off",
    }
    return aliases.get(mode, mode if mode in GUARD_MODES else DEFAULT_GUARD_MODE)


def apply_response_guard_mode(result: GuardResult, mode: str | None = None) -> GuardResult:
    """Apply guard mode and repeat suppression to one violation result."""
    mode = mode or response_guard_mode()
    if not result.blocked:
        return replace(result, mode=mode)
    if mode == "strict":
        return replace(result, mode=mode)
    if mode in {"warn-only", "off"}:
        return replace(result, blocked=False, suppressed=True, mode=mode, suppression_reason=mode)

    state_key = _guard_state_key(result.user_id, result.terms)
    if mode == "block-once":
        if _guard_state_contains(state_key, mode):
            return replace(
                result,
                blocked=False,
                suppressed=True,
                mode=mode,
                suppression_reason="already_blocked_for_prompt",
            )
        _record_guard_block(state_key, result.user_id, result.terms, mode)
        return replace(result, mode=mode)

    adaptive_decision = _adaptive_decision(state_key, result.user_id, result.terms)
    if adaptive_decision["blocked"]:
        return replace(result, mode=mode)
    return replace(
        result,
        blocked=False,
        suppressed=True,
        mode=mode,
        suppression_reason=adaptive_decision["reason"],
    )


def reset_guard_state(user_id: str) -> None:
    """Reset per-prompt block-once state for a project namespace."""
    if not user_id:
        return
    state = _load_guard_state()
    entries = [
        entry for entry in state["entries"]
        if entry.get("user_id") != user_id or entry.get("mode") != "block-once"
    ]
    if len(entries) == len(state["entries"]):
        return
    _write_guard_state({"version": 1, "entries": entries})


def assistant_message_from_input(data: dict) -> str:
    """Extract final assistant text from host-specific hook payloads."""
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


def load_guard_sources(user_id: str) -> List[str]:
    """Load project-level skill and active source skills for one project namespace."""
    sources: list[str] = []

    try:
        storage.init_db()
        project_skill = storage.load_project_skill(user_id)
        if project_skill and str(project_skill.get("content") or "").strip():
            sources.append(str(project_skill["content"]))
        else:
            project_skill_file = storage.SKILL_DIR / user_id / PROJECT_SKILL_FILE
            sources.extend(_read_existing([project_skill_file]))
        sources.extend(
            skill.content
            for skill in storage.load_skills(user_id, include_pending=False)
            if skill.status == "active" and skill.content.strip()
        )
    except Exception:
        pass
    return sources


def extract_banned_terms(sources: Sequence[str]) -> tuple[str, ...]:
    terms: list[str] = []
    for policy in extract_guard_policies(sources):
        terms.extend(policy.forbidden_terms)
    return tuple(_dedupe_terms(terms))


def extract_guard_policies(sources: Sequence[str]) -> tuple[GuardPolicy, ...]:
    policies: list[GuardPolicy] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for source in sources:
        policy = _structured_guard_policy(source)
        if not policy:
            continue
        key = (policy.mode, tuple(term.casefold() for term in policy.forbidden_terms))
        if key in seen:
            continue
        seen.add(key)
        policies.append(policy)
    return tuple(policies)


def find_guard_violations(message: str, policies: Iterable[GuardPolicy]) -> list[str]:
    prose = _strip_code(message)
    terms: list[str] = []
    for policy in policies:
        for start, end, term in _find_term_hits(prose, policy.forbidden_terms):
            if _allowed_by_guard_policy(prose, start, end, term, policy):
                continue
            terms.append(term)
    return _dedupe_terms(terms)


def find_banned_terms(message: str, terms: Iterable[str]) -> list[str]:
    prose = _strip_code(message)
    return [term for _, _, term in _find_term_hits(prose, terms)]


def _find_term_hits(prose: str, terms: Iterable[str]) -> list[tuple[int, int, str]]:
    matches: list[tuple[int, int, str]] = []
    for term in sorted(_dedupe_terms(terms), key=len, reverse=True):
        for match in _term_matches(prose, term):
            matches.append((match.start(), match.end(), term))

    selected: list[tuple[int, int, str]] = []
    for start, end, term in sorted(matches, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(start < used_end and end > used_start for used_start, used_end, _ in selected):
            continue
        selected.append((start, end, term))

    return selected


def stop_hook_output(result: GuardResult) -> str:
    """Serialize the JSON shape expected by Stop hooks."""
    if not result.blocked:
        return ""
    return json.dumps(
        {"decision": "block", "reason": result.reason},
        ensure_ascii=False,
    )


def blocking_stop_hook_supported(
    environment: Mapping[str, str], runtime_path: Path | None = None
) -> bool:
    """Return whether the active host can safely replay a blocking Stop hook."""
    if environment.get("CODEX_PLUGIN_ROOT"):
        return False
    candidates = [
        environment.get("CLAUDE_PLUGIN_ROOT", ""),
        environment.get("CODEX_HOME", ""),
        str(runtime_path or ""),
    ]
    return not any("/.codex/" in value.replace("\\", "/") for value in candidates)


def _guard_state_key(user_id: str, terms: Sequence[str]) -> str:
    payload = json.dumps(
        {
            "user_id": user_id,
            "terms": sorted(term.casefold() for term in terms),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _guard_state_contains(state_key: str, mode: str) -> bool:
    state = _load_guard_state()
    return any(
        entry.get("key") == state_key and entry.get("mode") == mode
        for entry in state["entries"]
    )


def _record_guard_block(state_key: str, user_id: str, terms: Sequence[str], mode: str) -> None:
    state = _load_guard_state()
    entries = [
        entry for entry in state["entries"]
        if not (entry.get("key") == state_key and entry.get("mode") == mode)
    ]
    entries.append(
        {
            "key": state_key,
            "user_id": user_id,
            "terms": list(terms),
            "mode": mode,
            "timestamp": time.time(),
        }
    )
    _write_guard_state({"version": 1, "entries": entries[-GUARD_STATE_MAX_ENTRIES:]})


def _adaptive_decision(state_key: str, user_id: str, terms: Sequence[str]) -> dict:
    state = _load_guard_state()
    entries = [
        entry for entry in state["entries"]
        if not (entry.get("key") == state_key and entry.get("mode") == "adaptive")
    ]
    existing = next(
        (
            entry for entry in state["entries"]
            if entry.get("key") == state_key and entry.get("mode") == "adaptive"
        ),
        {},
    )
    cooldown_remaining = int(existing.get("cooldown_remaining", 0) or 0)
    block_count = int(existing.get("block_count", 0) or 0)
    suppress_count = int(existing.get("suppress_count", 0) or 0)

    if cooldown_remaining > 0:
        entries.append({
            **existing,
            "key": state_key,
            "user_id": user_id,
            "terms": list(terms),
            "mode": "adaptive",
            "cooldown_remaining": cooldown_remaining - 1,
            "suppress_count": suppress_count + 1,
            "timestamp": time.time(),
        })
        _write_guard_state({"version": 1, "entries": entries[-GUARD_STATE_MAX_ENTRIES:]})
        return {"blocked": False, "reason": "adaptive_cooldown"}

    next_block_count = block_count + 1
    next_cooldown = min(2 ** (next_block_count - 1), ADAPTIVE_MAX_COOLDOWN)
    entries.append({
        "key": state_key,
        "user_id": user_id,
        "terms": list(terms),
        "mode": "adaptive",
        "block_count": next_block_count,
        "cooldown_remaining": next_cooldown,
        "suppress_count": suppress_count,
        "timestamp": time.time(),
    })
    _write_guard_state({"version": 1, "entries": entries[-GUARD_STATE_MAX_ENTRIES:]})
    return {"blocked": True, "reason": "adaptive_block"}


def _load_guard_state() -> dict:
    try:
        data = json.loads(GUARD_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {}

    entries = data.get("entries")
    if not isinstance(entries, list):
        entries = []

    cutoff = time.time() - GUARD_STATE_TTL_SECONDS
    active_entries = [
        entry for entry in entries
        if isinstance(entry, dict) and _entry_timestamp(entry) >= cutoff
    ]
    return {"version": 1, "entries": active_entries[-GUARD_STATE_MAX_ENTRIES:]}


def _write_guard_state(state: dict) -> None:
    try:
        GUARD_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = GUARD_STATE_PATH.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(GUARD_STATE_PATH)
    except OSError:
        return


def _entry_timestamp(entry: dict) -> float:
    try:
        return float(entry.get("timestamp", 0))
    except (TypeError, ValueError):
        return 0.0


def _read_existing(paths: Iterable[Path]) -> list[str]:
    contents: list[str] = []
    for path in paths:
        try:
            if path.exists():
                contents.append(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return contents


def _allowed_by_guard_policy(
    prose: str,
    start: int,
    end: int,
    term: str,
    policy: GuardPolicy,
) -> bool:
    if policy.mode != EVIDENCE_BASED_MODE or not policy.allow_evidence_gap_disclosure:
        return False
    if term.casefold() in {item.casefold() for item in policy.strict_terms}:
        return False

    sentence = _surrounding_sentence(prose, start, end)
    return (
        bool(policy.gap_markers)
        and bool(policy.evidence_markers)
        and _contains_any(sentence, policy.gap_markers)
        and _contains_any(sentence, policy.evidence_markers)
    )


def _surrounding_sentence(text: str, start: int, end: int) -> str:
    left = start
    while left > 0 and text[left - 1] not in "\n。！？.!?；;":
        left -= 1
    right = end
    while right < len(text) and text[right] not in "\n。！？.!?；;":
        right += 1
    return text[left:right].strip()


def _contains_any(text: str, markers: Iterable[str]) -> bool:
    haystack = text.casefold()
    return any(marker and marker.casefold() in haystack for marker in markers)


def _structured_guard_policy(source: str) -> GuardPolicy | None:
    frontmatter = _frontmatter(source)
    if not frontmatter:
        return None
    guard_lines = _frontmatter_block(frontmatter, "response_guard")
    if not guard_lines:
        return None
    enabled = any(
        re.match(r"\s*enabled\s*:\s*true\s*$", line, re.IGNORECASE)
        for line in guard_lines
    )
    if not enabled:
        return None

    mode = "forbidden_terms"
    requires_evidence = False
    allow_evidence_gap_disclosure = False
    lists: dict[str, list[str]] = {
        "forbidden_terms": [],
        "strict_terms": [],
        "evidence_markers": [],
        "gap_markers": [],
    }
    current_list = ""
    for line in guard_lines:
        mode_match = re.match(r"\s*mode\s*:\s*(.+?)\s*$", line)
        if mode_match:
            mode = mode_match.group(1).strip().strip("\"'") or mode
            continue
        if re.match(r"\s*requires_evidence\s*:\s*true\s*$", line, re.IGNORECASE):
            requires_evidence = True
            continue
        if re.match(r"\s*allow_evidence_gap_disclosure\s*:\s*true\s*$", line, re.IGNORECASE):
            allow_evidence_gap_disclosure = True
            continue
        list_match = re.match(r"\s*(forbidden_terms|strict_terms|evidence_markers|gap_markers)\s*:", line)
        if list_match:
            current_list = list_match.group(1)
            continue
        if current_list:
            match = re.match(r"\s*-\s*(.+?)\s*$", line)
            if match:
                term = match.group(1).strip().strip("\"'")
                if term:
                    lists[current_list].append(term)
            elif line and not line.startswith((" ", "\t", "-")):
                break
    terms = tuple(_dedupe_terms(lists["forbidden_terms"]))
    if not terms:
        return None
    return GuardPolicy(
        mode=mode,
        forbidden_terms=terms,
        requires_evidence=requires_evidence,
        allow_evidence_gap_disclosure=allow_evidence_gap_disclosure,
        strict_terms=tuple(_dedupe_terms(lists["strict_terms"])),
        evidence_markers=tuple(_dedupe_terms(lists["evidence_markers"])),
        gap_markers=tuple(_dedupe_terms(lists["gap_markers"])),
    )


def _frontmatter(source: str) -> str:
    text = source.lstrip()
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    return text[3:end]


def _frontmatter_block(frontmatter: str, key: str) -> list[str]:
    lines = frontmatter.splitlines()
    block: list[str] = []
    collecting = False
    for line in lines:
        if re.match(rf"^{re.escape(key)}\s*:", line):
            collecting = True
            block.append(line)
            continue
        if collecting:
            if line and not line.startswith((" ", "\t", "-")):
                break
            block.append(line)
    return block


def _dedupe_terms(terms: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in terms:
        term = raw.strip()
        if not term:
            continue
        if len(term) == 1 and not _is_latin_term(term):
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(term)
    return out


def _strip_code(text: str) -> str:
    without_fences = FENCED_CODE_RE.sub(" ", text)
    return INLINE_CODE_RE.sub(" ", without_fences)


def _term_matches(text: str, term: str):
    if _is_latin_term(term):
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])",
            re.IGNORECASE,
        )
        return list(pattern.finditer(text))
    return list(re.finditer(re.escape(term), text))


def _is_latin_term(term: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_'\- ]*", term))
