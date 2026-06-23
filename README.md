# Chat2Skill

Automatically learn reusable skills from your assistant conversations.

After each session, Chat2Skill analyzes the conversation for corrections,
preferences, and constraints, distills them into `SKILL.md` files, and
injects the relevant ones into your future sessions — so your agent stops
repeating the same mistakes. It is domain-general: coding workflows are the
first-class integration target, while the same mechanism works for support,
research, writing, operations, sales, education, and other assistant domains
that produce usable transcripts.

Works best with **Claude Code**, **Codex**, and **Cursor**. Other agents
can use Chat2Skill when they support lifecycle hooks or can run the
included CLI scripts.


## What the Algorithm Produces

Chat2Skill extracts two levels of reusable guidance:

- **Atomized skills**: focused `SKILL.md` files for one interaction preference,
  procedure, constraint, success pattern, or failure pattern.
- **Project skill**: a synthesized `PROJECT_SKILL.md` that merges active
  atomized skills into a compact project-level instruction file.

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
| 1. Retrieve active skills for the next assistant session |
+---------------------------+-----------------------------+
                            |
                            v
+---------------------------------------------------------+
| 2. Inject relevant skills / PROJECT_SKILL.md into prompt |
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
| 7. Rebuild PROJECT_SKILL.md and update user profile      |
+---------------------------+-----------------------------+
                            |
                            +------------- back to step 1
```

The algorithm is a feedback loop, not a one-shot workflow. Each completed
session can change the skill bank; the next session retrieves from that updated
bank; later user feedback reinforces, edits, rejects, or ages out earlier
skills.

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
Stop hook ──► response guard ──► completion review ──► continue on violation
     │
     └────► queue ──► worker ─────────────► POST /v1/extract
                          │                 (stateless algorithm,
   ~/.chat2skill/ ◄───────┘                  your own LLM api key)
   skills + profile + history     ◄──────── skill + profile + replay
                                            POST /v1/project-skill
UserPromptSubmit hook ◄── local retrieval   (project summary + detailed skills)
```

- **Your data stays local.** Skills, profile, and history live in
  `~/.chat2skill/` (SQLite + markdown files). The cloud runs the
  extraction algorithm statelessly and stores nothing.
- **Bring your own key.** Extraction LLM calls use *your* api key
  (OpenAI-compatible, e.g. OpenAI/DeepSeek). The key is sent with each
  request, used in memory, never persisted or logged server-side.
  Without a key, the server falls back to lower-quality heuristics.
- **Response guard.** When a project summary contains a high-confidence
  deterministic wording constraint, the Stop hook checks the final assistant
  message locally. The learned rule is evidence-based: verified facts must use
  definitive wording; evidence gaps must name the missing source material,
  data, record, document, log, test, command output, or code and the next
  validation step. The guard only reads explicit `response_guard` frontmatter,
  never prose examples or code identifiers. The default guard mode is
  `adaptive`: repeated violations are throttled with a growing cooldown and
  logged without blocking every turn.
- **Completion review.** The Stop hook can reconcile the latest actionable
  user request against the final assistant response. For concrete deliverables,
  it checks that completion claims include evidence, covered scope, unchecked
  scope, or an explicit verification gap. This creates a generic requirement
  reconciliation loop across coding, writing, reports, research, operations,
  and other task types.
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
| `CHAT2SKILL_RESPONSE_GUARD` | unset | `adaptive` | Stop response guard mode. Use `adaptive`, `block-once`, `strict`, `warn-only`, or `off`. Structured `response_guard.mode: evidence_based_terms` allows explicit evidence-gap disclosure while still blocking unsupported hedging. |
| `CHAT2SKILL_COMPLETION_REVIEW` | unset | `strict` | Stop completion review mode. Use `strict`, `warn-only`, or `off`. Strict mode blocks high-confidence gaps where the final response claims completion without matching the original request to evidence and scope. |

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

Claude Code loads the standard `hooks/hooks.json` file automatically,
and `${CLAUDE_PLUGIN_ROOT}` resolves to the installed plugin directory —
no path setup needed.

### 2b. Codex

Install from the Chat2Skill marketplace:

```bash
codex plugin marketplace add rxacc/chat2skill
codex
```

Open `/plugins`, select the `chat2skill` marketplace, and install
`chat2skill`.

For non-interactive install:

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

`install.sh` writes `hooks.json` with absolute paths for your clone
location and creates the config file if missing.

### 2c. Cursor

Cursor supports native plugins with `.cursor-plugin/plugin.json`.

In Cursor:

1. Open **Settings -> Plugins**.
2. Paste this repository URL into **Search or Paste Link**:

```text
https://github.com/rxacc/chat2skill
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
- session-end learning: `python3 <plugin-root>/scripts/hook_stop.py`
- session-end response guard: `python3 <plugin-root>/scripts/hook_stop_response_guard.py`
- session-end completion review: `python3 <plugin-root>/scripts/hook_stop_completion_review.py`

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
- **Reconcile completion:** a stop/session-end hook with access to the
  transcript and final assistant message that can run
  `scripts/hook_stop_completion_review.py`.

| Agent | Current support | Notes |
| --- | --- | --- |
| Claude Code | Native plugin marketplace | Full automatic support through `.claude-plugin/marketplace.json`, the `chat2skill` skill, and standard `hooks/hooks.json` with `UserPromptSubmit` + `Stop` learning + Stop response guard + Stop completion review. |
| Codex | Native plugin/local installer | Full automatic support through `.codex-plugin/plugin.json` and `install.sh`, which writes absolute hook paths for the local clone, including the Stop response guard and completion review. |
| Cursor | Native plugin + project rule | Supported through `.cursor-plugin/plugin.json`, `hooks/cursor-hooks.json`, `.cursor/rules/chat2skill.mdc`, and the `chat2skill` skill. Stop learning works from Cursor transcripts, and the response guard runs when Cursor provides final response text. Dynamic per-prompt context injection is limited by Cursor's current `beforeSubmitPrompt` hook behavior. |
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
├── skills/<user>/PROJECT_SKILL.md   # injected before each conversation and read by response guard
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
