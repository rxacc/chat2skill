# Chat2Skill

Automatically learn reusable skills from your coding-agent conversations.

After each session, Chat2Skill analyzes the conversation for corrections,
preferences, and constraints, distills them into `SKILL.md` files, and
injects the relevant ones into your future sessions — so your agent stops
repeating the same mistakes.

Works best with **Claude Code**, **Codex**, and **Cursor**. Other agents
can use Chat2Skill when they support lifecycle hooks or can run the
included CLI scripts.

## How it works

```
your machine                                Chat2Skill cloud
─────────────────────────────────────       ─────────────────────────
Stop hook ──► queue ──► worker ───────────► POST /v1/extract
                          │                 (stateless algorithm,
   ~/.chat2skill/ ◄───────┘                  your own LLM api key)
   skills + profile + history     ◄──────── skill + profile + replay
                                            POST /v1/project-skill
UserPromptSubmit hook ◄── local retrieval   (project summary)
```

- **Your data stays local.** Skills, profile, and history live in
  `~/.chat2skill/` (SQLite + markdown files). The cloud runs the
  extraction algorithm statelessly and stores nothing.
- **Bring your own key.** Extraction LLM calls use *your* api key
  (OpenAI-compatible, e.g. OpenAI/DeepSeek). The key is sent with each
  request, used in memory, never persisted or logged server-side.
  Without a key, the server falls back to lower-quality heuristics.
- **Cost.** A typical extraction makes ~4 LLM calls on your key
  (detect, analyze, generate, judge); replay validation against your
  history adds up to 5 more. Conversations are windowed (last ~40
  messages) so long sessions stay cheap. Extraction only triggers when
  a correction/constraint signal is detected, not on every session.

## Install

### 1. Configure

```bash
mkdir -p ~/.chat2skill
cp config.example.json ~/.chat2skill/config.json
# edit ~/.chat2skill/config.json: set llm.api_key (and base_url/model)
```

Use one config file. For OpenAI, write `~/.chat2skill/config.json` like this:

```json
{
  "api_url": "https://api.chat2skill.com",
  "user_id": "alice",
  "llm": {
    "api_key": "your-openai-compatible-api-key",
    "base_url": null,
    "model": "gpt-4.1"
  }
}
```

For DeepSeek, write `~/.chat2skill/config.json` like this:

```json
{
  "api_url": "https://api.chat2skill.com",
  "user_id": "alice",
  "llm": {
    "api_key": "your-deepseek-api-key",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat"
  }
}
```

These are the equivalent environment variables. You only need environment
variables if you prefer shell config or need to override the JSON file.

| Environment variable | JSON key | Default | Description |
| --- | --- | --- | --- |
| `CHAT2SKILL_API_URL` | `api_url` | `https://api.chat2skill.com` | Chat2Skill API endpoint used for extraction and project-skill generation. |
| `OPENAI_API_KEY` | `llm.api_key` | unset | Your OpenAI-compatible LLM API key. If unset, extraction falls back to lower-quality heuristics. |
| `OPENAI_BASE_URL` | `llm.base_url` | `null` | Optional OpenAI-compatible base URL. Use `null` for OpenAI; use `https://api.deepseek.com` for DeepSeek. |
| `CHAT2SKILL_MODEL` | `llm.model` | `gpt-4.1` | Model used for detect/analyze/generate/judge calls. |
| `CHAT2SKILL_USER_ID` | `user_id` | system username | Base namespace for local skills and profile data. Project-specific skills use `<user>__project__<slug>`. |

### 2a. Claude Code

Install from the Chat2Skill marketplace:

```bash
claude plugin marketplace add https://github.com/rexia01/Chat2Skill
claude plugin install chat2skill@chat2skill
```

For local development, load the plugin for one session:

```bash
claude --plugin-dir ~/plugins/chat2skill
```

Claude Code loads the standard `hooks/hooks.json` file automatically,
and `${CLAUDE_PLUGIN_ROOT}` resolves to the installed plugin directory —
no path setup needed.

### 2b. Codex

```bash
git clone https://github.com/rexia01/Chat2Skill.git ~/plugins/chat2skill
cd ~/plugins/chat2skill && ./install.sh
```

`install.sh` writes `hooks.json` with absolute paths for your clone
location and creates the config file if missing.

### 2c. Cursor

Cursor supports native plugins with `.cursor-plugin/plugin.json`.

In Cursor:

1. Open **Settings -> Plugins**.
2. Paste this repository URL into **Search or Paste Link**:

```text
https://github.com/rexia01/Chat2Skill
```

The Cursor plugin uses:

- `hooks/cursor-hooks.json` for Cursor-format hooks.
- `${CURSOR_PLUGIN_ROOT}` for installed plugin paths.
- `.cursor/rules/chat2skill.mdc` as an always-on project rule.
- `sessionStart` to provide the current project summary when Cursor
  accepts hook context.
- `stop` to learn from the newest Cursor agent transcript under
  `~/.cursor/projects/*/agent-transcripts/`.

Important Cursor limitation: Cursor plugins do support hooks and skills,
but Cursor's `beforeSubmitPrompt` hook currently cannot inject dynamic
per-prompt context into the model. For prompt-specific retrieval in
Cursor, use the `chat2skill` skill or run:

```bash
python3 scripts/retrieve_for_prompt.py "your current task"
```

### 2d. OpenCode

Run OpenCode from a checkout of this repository. `opencode.json` loads
`.opencode/plugins/chat2skill.mjs`, which calls the same retrieval CLI and
adds relevant snippets to the system prompt.

```json
{ "plugin": ["./.opencode/plugins/chat2skill.mjs"] }
```

### 2e. Other agents

If your agent supports hooks, point them at:
- prompt-submit: `python3 <plugin-root>/scripts/hook_user_prompt_submit.py`
- session-end: `python3 <plugin-root>/scripts/hook_stop.py`

No hooks? Use the CLIs:

```bash
# after a session: learn from the newest transcript
python3 scripts/update_from_transcript.py --latest

# before a task: print a prompt snippet with relevant skills
python3 scripts/retrieve_for_prompt.py "refactor the auth module"
```

For agents that only support repository instructions, copy or keep the
matching adapter file:

- Cursor: `.cursor/rules/chat2skill.mdc`
- Windsurf/Cascade: `.windsurf/rules/chat2skill.md`
- Cline: `.clinerules/chat2skill.md`
- GitHub Copilot: `.github/copilot-instructions.md`
- Kiro: `.kiro/steering/chat2skill.md`
- Generic agents/Aider: `AGENTS.md`

## Agent Support

Chat2Skill needs two capabilities for the full automatic loop:

- **Learn after a session:** a stop/session-end hook that can run
  `scripts/hook_stop.py`.
- **Retrieve before work:** a prompt/session-start hook or skill workflow
  that can inject or load the output of `scripts/retrieve_for_prompt.py`.

| Agent | Current support | Notes |
| --- | --- | --- |
| Claude Code | Native plugin marketplace | Full automatic support through `.claude-plugin/marketplace.json`, the `chat2skill` skill, and standard `hooks/hooks.json` with `UserPromptSubmit` + `Stop`. |
| Codex | Native plugin/local installer | Full automatic support through `.codex-plugin/plugin.json` and `install.sh`, which writes absolute hook paths for the local clone. |
| Cursor | Native plugin + project rule | Supported through `.cursor-plugin/plugin.json`, `hooks/cursor-hooks.json`, `.cursor/rules/chat2skill.mdc`, and the `chat2skill` skill. Stop learning works from Cursor transcripts. Dynamic per-prompt context injection is limited by Cursor's current `beforeSubmitPrompt` hook behavior. |
| OpenCode | Server plugin + command | `opencode.json` loads `.opencode/plugins/chat2skill.mjs`, which calls `retrieve_for_prompt.py` and appends relevant snippets to the system prompt. `.opencode/command/chat2skill.md` adds a manual command prompt. |
| GitHub Copilot | Repository instructions | `.github/copilot-instructions.md` tells Copilot how to run Chat2Skill CLI retrieval/update. Local Copilot CLI hooks can also call Chat2Skill; cloud/ephemeral agents are not equivalent to local persistent hooks. |
| Kimi Code CLI | Skills/hooks capable | Configure `UserPromptSubmit`/`Stop` equivalents to call the hook scripts, or use the skill/CLI workflow. |
| Windsurf / Cascade | Project rule + hooks capable | `.windsurf/rules/chat2skill.md` provides the project rule. Configure workspace/user hooks to call the hook scripts when available. |
| Kiro | Steering rule + hooks/export capable | `.kiro/steering/chat2skill.md` provides the steering rule. Use hooks where available, or export/process transcripts manually with `update_from_transcript.py`. |
| Cline | Project rule | `.clinerules/chat2skill.md` provides the project rule. Use the CLI scripts for retrieval/update. |
| Aider / generic agents | `AGENTS.md` | `AGENTS.md` gives portable instructions for agents that read repository guidance. |
| Gemini CLI | Extension/hooks capable | Use the generic hook commands or CLI scripts. A dedicated Gemini extension manifest is not included yet. |
| Google Antigravity | Plugin/hooks capable | Use the generic hook commands or CLI scripts. A dedicated Antigravity plugin manifest is not included yet. |
| Continue | Manual/partial | Rules, prompts, and MCP are useful, but no verified lifecycle hook path for the full Chat2Skill loop is included. |
| Roo Code | Manual/legacy | Use the CLI scripts only unless your local fork exposes compatible hooks. |

See [docs/agent-portability.md](docs/agent-portability.md) for the full
adapter map.

## Requirements

- Python 3.10+ (standard library only — no pip installs)
- A Chat2Skill API endpoint (`api_url` in config)
- Optional: an OpenAI-compatible LLM api key for high-quality extraction

## Data layout

```
~/.chat2skill/
├── config.json                  # endpoint + your LLM credentials
├── chat2skill.db                # conversations, skills, profile
├── skills/<user>/<name>/SKILL.md
├── skills/<user>/PROJECT_SKILL.md   # injected before each conversation
└── hook-events.log
```

Skills are namespaced per project (`<user>__project__<slug>`), so what
you learn in one repo doesn't leak into another.

## Privacy

- Conversations are sent to the Chat2Skill API for analysis, processed
  in memory, and not persisted server-side. Server logs contain metadata
  only (session id, error type) — never message content or api keys.
- Agent system prompts, environment banners, and tool noise are stripped
  locally before upload (see `scripts/chat2skill/transcripts.py`).
- To stop all uploads, remove the Stop hook or unset `api_url`.

## License

MIT
