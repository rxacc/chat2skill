# Agent Portability

Chat2Skill is an agent-portable skill distribution. The core behavior lives in
`skills/`, `scripts/`, and `hooks/`; host-specific files are thin adapters that
load the same behavior in each coding agent.

## Supported Adapters

| Host | Files | Notes |
| --- | --- | --- |
| Claude Code | `.claude-plugin/`, `hooks/hooks.json`, `skills/` | Full plugin marketplace install with the `chat2skill` skill, `UserPromptSubmit`, Stop learning, and Stop response guard hooks. |
| Codex | `.codex-plugin/plugin.json`, `install.sh`, generated `hooks.json`, `skills/` | Plugin/local install with absolute hook paths for the checkout, including Stop learning and Stop response guard. |
| Cursor | `.cursor-plugin/`, `.cursor-plugin/hooks.json`, `.cursor/rules/chat2skill.mdc` | Native plugin plus always-on project rule. `stop` learns from Cursor transcripts and runs the response guard when Cursor provides final response text; prompt-specific retrieval should use the skill or CLI because Cursor's current `beforeSubmitPrompt` hook does not inject dynamic context. |
| OpenCode | `opencode.json`, `.opencode/plugins/chat2skill.mjs`, `.opencode/command/chat2skill.md` | Server plugin calls `retrieve_for_prompt.py` and appends relevant snippets to the system prompt. |
| GitHub Copilot | `.github/copilot-instructions.md` | Repository instruction file that tells Copilot how to call the Chat2Skill CLI. |
| Windsurf | `.windsurf/rules/chat2skill.md` | Project rule for Cascade/Windsurf. |
| Cline | `.clinerules/chat2skill.md` | Project rule. |
| Kiro | `.kiro/steering/chat2skill.md` | Steering rule; copy globally or keep in a project. |
| Generic agents | `AGENTS.md`, `skills/chat2skill/SKILL.md` | Portable instruction file or direct skill loading. |

## Adapter Rule

Keep adapters thin. When a host supports skills or hooks, point it at the
existing `skills/`, `scripts/`, and `hooks/` files. When a host only supports
project instructions, keep its copied rule text aligned with `AGENTS.md`.

## Portable Behavior

- `skills/chat2skill/SKILL.md`: manual update/retrieve workflow.
- `scripts/hook_user_prompt_submit.py`: prompt/session-start retrieval hook.
- `scripts/hook_stop.py`: session-end learning hook.
- `scripts/hook_stop_response_guard.py`: session-end final-message guard for hard wording constraints.
- `scripts/retrieve_for_prompt.py`: manual or plugin-driven retrieval.
- `scripts/update_from_transcript.py`: manual transcript processing.
