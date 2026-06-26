---
name: chat2skill
description: Update or retrieve user-specific skills learned from past coding-agent conversations via Chat2Skill.
---

## When to Use
Use this skill when the user asks to:
- update learned skills from recent sessions or conversations
- retrieve learned skills before starting a new task
- inspect where Chat2Skill stored generated SKILL.md files

## Local Data
All learned data lives under `~/.chat2skill/` (override with `CHAT2SKILL_HOME`):
- `config.json` — API endpoint and the user's own LLM credentials
- `chat2skill.db` — conversations, skills, profile (SQLite)
- `skills/<user_id>/<skill-name>/SKILL.md` — generated skills
- `skills/<user_id>/PROJECT_SKILL.md` — project-level summary
- `hook-events.log` — hook activity log
- Stop hooks include a response guard for learned hard wording constraints.

## Update Skills From the Latest Session

```bash
python3 <plugin-root>/scripts/update_from_transcript.py --latest
```

To process a specific transcript:

```bash
python3 <plugin-root>/scripts/update_from_transcript.py --input /path/to/session.jsonl
```

## Retrieve Skills For A New Task

```bash
python3 <plugin-root>/scripts/retrieve_for_prompt.py "the user's current task text"
```

Inject the returned prompt snippet into the working context before responding.
The automatic `UserPromptSubmit` hook injects the project summary plus
prompt-relevant detailed skills when both exist.

## Rules
- Extraction runs on the Chat2Skill cloud using the api key from `~/.chat2skill/config.json`. Without a key, the server falls back to lower-quality heuristics.
- Do not edit generated skills manually unless the user explicitly asks.
- After updating, show the user the generated skill path and result summary.
