# Chat2Skill

Automatically learn reusable skills and project memory from your assistant conversations.

After each session, Chat2Skill analyzes the conversation for corrections,
preferences, constraints, and project facts, distills them into local memory
and `SKILL.md` files, and injects the relevant ones into your future sessions.
It is domain-general: coding workflows are the first-class integration target,
while the same mechanism works for support, research, writing, operations,
sales, education, and other assistant domains that produce usable transcripts.

Works best with **Claude Code**, **Codex**, and **Cursor**. Other agents
can use Chat2Skill when they support lifecycle hooks or can run the
included CLI scripts.


## What the Algorithm Produces

Chat2Skill extracts reusable project context in two stores:

- **Atomized skills**: focused `SKILL.md` files for one interaction preference,
  procedure, constraint, success pattern, or failure pattern.
- **Project memory**: project facts, decisions, procedures, and
  warnings stored in the local SQLite database and retrieved dynamically.
- **Project skill**: a synthesized `PROJECT_SKILL.md` that merges active
  atomized skills into a compact project-level instruction file for human
  review and response-guard policy.

A skill is not meant to remember one transcript. It captures a generalizable
behavior that would change future assistant behavior across similar situations.

## Core Concepts

| Concept | Meaning |
| --- | --- |
| Conversation | Recent assistant/user messages for one session. Long sessions are trimmed to the latest analysis window. |
| Signal | Evidence that something should be learned: correction, explicit constraint, negative feedback, or stable behavioral preference. |
| Analysis | A structured diagnosis of what went wrong or what worked, including failure type, root cause, confidence, and proposed action. |
| Proposal | The create/edit/discard decision for a skill candidate. |
| Memory item | Evidence extracted before materializing a skill, such as failure cause, failure memory, success, or constraint. |
| Skill | A validated, actionable `SKILL.md` with metadata such as confidence, evidence count, language, replay score, and status. |
| Response guard | Optional frontmatter policy for hard wording constraints, such as evidence-based deterministic wording. |

## Learning Loop

```text
+---------------------------------------------------------+
| 1. Retrieve relevant project memory and active skills    |
+---------------------------+-----------------------------+
                            |
                            v
+---------------------------------------------------------+
| 2. Inject retrieved project memory + skills into prompt  |
+---------------------------+-----------------------------+
                            |
                            v
+---------------------------------------------------------+
| 3. Assistant works; user accepts, corrects, or constrains |
+---------------------------+-----------------------------+
                            |
                            v
+---------------------------------------------------------+
| 4. Extract learning signals at session end               |
+---------------------------+-----------------------------+
                            |
                            v
+---------------------------------------------------------+
| 5. Create / edit / discard atomized skill candidates     |
+---------------------------+-----------------------------+
                            |
                            v
+---------------------------------------------------------+
| 6. Validate, replay, merge, and store active skills      |
+---------------------------+-----------------------------+
                            |
                            v
+---------------------------------------------------------+
| 7. Rebuild PROJECT_SKILL.md and update local profile     |
+---------------------------+-----------------------------+
                            |
                            +------------- back to step 1
```

The algorithm is a feedback loop, not a one-shot workflow. Each completed
session can change project memory and the skill bank; the next session retrieves
from those updated local stores; later user feedback reinforces, edits, rejects,
or ages out earlier context.

There is still a single extraction pass inside the loop. That pass is:

```text
recent conversation + existing skills + profile
  -> detect signals
  -> analyze root cause
  -> propose create/edit/discard
  -> generate SKILL.md
  -> quality gate
  -> judge
  -> optional replay
  -> active/rejected/no-action result
```

The LLM path uses Proposer, Generator, and Judge style stages. When no LLM is
available, the loop still runs with keyword detection and template-based
generation.

## Loop Layers

Chat2Skill has three nested loops:

1. **Session learning loop**: retrieve skills before work, observe user feedback
   during work, extract or update skills after work, then use the updated skill
   bank next time.
2. **Candidate refinement loop**: if the judge rejects a generated skill, feed
   the judge weakness back into generation and retry up to two times.
3. **Maintenance loop**: score active skills by utilization, replay
   effectiveness, recency, and overlap; merge near-duplicates and archive old
   weak skills instead of letting the prompt grow forever.

## How it works

```
your machine                                Chat2Skill cloud
─────────────────────────────────────       ─────────────────────────
Stop hook ──► response guard ──► continue on violation
     │
     └────► queue ──► worker ─────────────► POST /v1/extract
                          │                 (stateless algorithm,
   ~/.chat2skill/ ◄───────┘                  your own LLM api key)
   skills + profile + history     ◄──────── skill + profile + replay
                                            POST /v1/project-skill
UserPromptSubmit hook ◄── local retrieval   (project skill + detailed skills)
```

- **Your data stays local.** Skills, profile, and history live in
  `~/.chat2skill/` (SQLite + markdown files). The cloud runs the
  extraction algorithm statelessly and stores nothing.
- **Bring your own key.** Extraction LLM calls use *your* api key
  (OpenAI-compatible, e.g. OpenAI/DeepSeek). The key is sent with each
  request, used in memory, never persisted or logged server-side.
  Without a key, the server falls back to lower-quality heuristics.
- **Response guard.** When a project skill contains a high-confidence
  deterministic wording constraint, the Stop hook checks the final assistant
  message locally. The learned rule is evidence-based: verified facts must use
  definitive wording; evidence gaps must name the missing source material,
  data, record, document, log, test, command output, or code and the next
  validation step. The guard only reads explicit `response_guard` frontmatter,
  never prose examples or code identifiers. The default guard mode is
  `adaptive`: repeated violations are throttled with a growing cooldown and
  logged without blocking every turn.
- **Cost.** A typical extraction makes ~4 LLM calls on your key
  (detect, analyze, generate, judge); replay validation against your
  history adds up to 5 more. Conversations are windowed (last ~40
  messages) so long sessions stay cheap. Extraction only triggers when
  a correction/constraint signal is detected, not on every session.

## Install

### 1. Configure

Cross-platform initialization:

```bash
python3 scripts/chat2skill_init.py
```

On Windows:

```powershell
python .\scripts\chat2skill_init.py
```

The command creates the local data directory, config file, SQLite database,
and skills directory:

- macOS/Linux: `~/.chat2skill/`
- Windows: `%USERPROFILE%\.chat2skill\`

Manual setup is equivalent:

```bash
mkdir -p ~/.chat2skill
cp config.example.json ~/.chat2skill/config.json
# edit ~/.chat2skill/config.json: set api_url and llm.api_key
```

Use one config file. Chat2Skill calls the stateless learn API for extraction
and stores returned project memory, conversations, skills, and profiles in
`~/.chat2skill/c2s.db`. Prompt retrieval runs locally from that database and
always injects retrieved project memory plus relevant skills. Rendered skill
files stay under `~/.chat2skill/skills/`.

For OpenAI-compatible models, write `~/.chat2skill/config.json` like this:

```json
{
  "api_url": "https://api.chat2skill.com",
  "user_id": "alice",
  "memory": {
    "target_model": "generic",
    "token_budget": 4000,
    "memory_ratio": 0.6,
    "skill_top_k": 6,
    "prompt_memory_top_k": 12,
    "learn_memory_top_k": 40,
    "learn_skill_top_k": 20
  },
  "llm": {
    "api_key": "your-openai-compatible-api-key",
    "base_url": null,
    "model": "gpt-4.1"
  },
  "embedding": {
    "provider": "local_transformers",
    "model": "Snowflake/snowflake-arctic-embed-xs",
    "dimensions": 384
  }
}
```

For DeepSeek, write `~/.chat2skill/config.json` like this:

```json
{
  "api_url": "https://api.chat2skill.com",
  "user_id": "alice",
  "memory": {
    "target_model": "generic",
    "token_budget": 4000,
    "memory_ratio": 0.6,
    "skill_top_k": 6,
    "prompt_memory_top_k": 12,
    "learn_memory_top_k": 40,
    "learn_skill_top_k": 20
  },
  "llm": {
    "api_key": "your-deepseek-api-key",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat"
  },
  "embedding": {
    "provider": "local_transformers",
    "model": "Snowflake/snowflake-arctic-embed-xs",
    "dimensions": 384
  }
}
```

For a remote OpenAI-compatible embedding endpoint, replace the `embedding`
block with:

```json
{
  "embedding": {
    "api_key": "your-embedding-api-key",
    "base_url": "http://127.0.0.1:8080/v1",
    "model": "BAAI/bge-large-en-v1.5"
  }
}
```

These are the equivalent environment variables. You only need environment
variables if you prefer shell config or need to override the JSON file.

## Local Admin UI

Chat2Skill includes a local-only management page for reviewing and managing
stored project memory and skills.

```bash
python3 scripts/chat2skill_admin.py
# opens http://127.0.0.1:8765 with a one-time local admin token
```

The admin server binds to `127.0.0.1` by default and reads/writes only the
local `~/.chat2skill/c2s.db` database. It can:

- list Chat2Skill projects discovered from the local database
- view and rebuild the project-level `PROJECT_SKILL.md`
- search, edit, archive, activate, and delete atomized skills
- search, edit, archive, activate, and delete project memories
- inspect the source skill snapshot used for the current project skill version

For frontend development, run the Vite shell separately:

```bash
cd admin/frontend
npm install
npm run dev
```

Keep the Python admin server running on `127.0.0.1:8765` while using the Vite
dev server; Vite proxies `/api` requests to the Python backend.

| Environment variable | JSON key | Default | Description |
| --- | --- | --- | --- |
| `CHAT2SKILL_API_URL` | `api_url` | `https://api.chat2skill.com` | Chat2Skill API endpoint used for stateless learn/extract calls. |
| `CHAT2SKILL_MEMORY_TARGET_MODEL` | `memory.target_model` | `generic` | Reserved renderer target for API-compatible payloads. |
| `CHAT2SKILL_MEMORY_TOKEN_BUDGET` | `memory.token_budget` | `4000` | Total prompt-injection token budget for memory plus skills. |
| `CHAT2SKILL_MEMORY_MEMORY_RATIO` | `memory.memory_ratio` | `0.6` | Fraction of retrieval budget initially allocated to memory. |
| `CHAT2SKILL_MEMORY_SKILL_TOP_K` | `memory.skill_top_k` | `6` | Maximum detailed skills injected by local prompt retrieval. |
| `OPENAI_API_KEY` | `llm.api_key` | unset | Your OpenAI-compatible LLM API key. If unset, extraction falls back to lower-quality heuristics. |
| `OPENAI_BASE_URL` | `llm.base_url` | `null` | Optional OpenAI-compatible base URL. Use `null` for OpenAI; use `https://api.deepseek.com` for DeepSeek. |
| `CHAT2SKILL_MODEL` | `llm.model` | `gpt-4.1` | Model used for detect/analyze/generate/judge calls. |
| `CHAT2SKILL_USER_ID` | `user_id` | system username | Base namespace for local skills and profile data. Project-specific skills use `<user>__project__<slug>`. |
| `CHAT2SKILL_RESPONSE_GUARD` | unset | `adaptive` | Stop response guard mode. Use `adaptive`, `block-once`, `strict`, `warn-only`, or `off`. Structured `response_guard.mode: evidence_based_terms` allows explicit evidence-gap disclosure while still blocking unsupported hedging. |

### 2a. Claude Code

Install from the Chat2Skill marketplace:

```bash
claude plugin marketplace add https://github.com/rxacc/chat2skill
claude plugin install chat2skill@chat2skill
```

For local development, load the plugin for one session:

```bash
claude --plugin-dir ~/plugins/chat2skill
```

Claude Code uses `.claude-plugin/hooks.json`, and `${CLAUDE_PLUGIN_ROOT}`
resolves to the installed plugin directory — no path setup needed.

### 2b. Codex

Install from the Chat2Skill marketplace:

```bash
codex plugin marketplace add rxacc/chat2skill
codex
```

Open `/plugins`, select the `chat2skill` marketplace, and install
`chat2skill`.

Codex uses `.codex-plugin/hooks.json`. The hook entrypoints initialize the local
data home on first hook/admin run:

- macOS/Linux: `~/.chat2skill/`
- Windows: `%USERPROFILE%\.chat2skill\`

```bash
codex plugin marketplace add rxacc/chat2skill
codex plugin add chat2skill@chat2skill
codex
```

For local development or manual hook generation:

```bash
git clone https://github.com/rxacc/chat2skill.git ~/plugins/chat2skill
cd ~/plugins/chat2skill && ./install.sh
```

`install.sh` refreshes known local plugin cache directories with agent-specific
hook files and creates the config file if missing.

### 2c. Cursor

Cursor supports native plugins with `.cursor-plugin/plugin.json`.

In Cursor:

1. Open **Settings -> Plugins**.
2. Paste this repository URL into **Search or Paste Link**:

```text
https://github.com/rxacc/chat2skill
```

The Cursor plugin uses:

- `.cursor-plugin/hooks.json` for Cursor-format hooks.
- `${CURSOR_PLUGIN_ROOT}` for installed plugin paths.
- `.cursor/rules/chat2skill.mdc` as an always-on project rule.
- `sessionStart` to provide the current project skill when Cursor
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
- session-end learning: `python3 <plugin-root>/scripts/hook_stop.py`
- session-end response guard: `python3 <plugin-root>/scripts/hook_stop_response_guard.py`

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
- **Enforce hard wording rules:** a stop/session-end hook with access to
  the final assistant message that can run `scripts/hook_stop_response_guard.py`.
  The default `adaptive` mode prevents repeated Stop-hook rewrite loops, and
  evidence-based rules distinguish verified conclusions from missing-evidence
  disclosures.

| Agent | Current support | Notes |
| --- | --- | --- |
| Claude Code | Native plugin marketplace | Full automatic support through `.claude-plugin/marketplace.json`, `.claude-plugin/hooks.json`, the `chat2skill` skill, `UserPromptSubmit`, Stop learning, and Stop response guard. |
| Codex | Native plugin/local installer | Full automatic support through `.codex-plugin/plugin.json`, `.codex-plugin/hooks.json`, and local cache refresh through `install.sh`, including the Stop response guard. |
| Cursor | Native plugin + project rule | Supported through `.cursor-plugin/plugin.json`, `.cursor-plugin/hooks.json`, `.cursor/rules/chat2skill.mdc`, and the `chat2skill` skill. Stop learning works from Cursor transcripts, and the response guard runs when Cursor provides final response text. Dynamic per-prompt context injection is limited by Cursor's current `beforeSubmitPrompt` hook behavior. |
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
- Node.js + npm for optional local embeddings through `Snowflake/snowflake-arctic-embed-xs`
- A Chat2Skill API endpoint (`api_url` in config)
- Optional: an OpenAI-compatible LLM api key for high-quality extraction

## Data layout

```
~/.chat2skill/
├── config.json                  # endpoint + your LLM credentials
├── c2s.db                       # conversations, skills, project_skills, profile, project memory
├── skills/<user>/<name>/SKILL.md
├── skills/<user>/PROJECT_SKILL.md   # human-readable project skill and response-guard input
└── hook-events.log
```

Skills are namespaced per project (`<user>__project__<slug>`), so what
you learn in one repo doesn't leak into another.

## Privacy

- Stop-hook transcripts are sent to the Chat2Skill API for stateless
  analysis, processed in memory, and not persisted server-side. Server logs
  contain metadata only (session id, error type) — never message content or
  api keys.
- Prompt retrieval does not call the cloud API. It loads top-K project
  memory and skills from local `~/.chat2skill/c2s.db`, applies the configured
  budget, and injects the compact result into the prompt.
- Agent system prompts, environment banners, and tool noise are stripped
  locally before upload (see `scripts/chat2skill/transcripts.py`).
- To stop all uploads, remove the Stop hook or unset `api_url`.

## License

MIT
