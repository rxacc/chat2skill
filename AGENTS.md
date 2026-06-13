# Chat2Skill

Use Chat2Skill when a task can benefit from reusable user/project skills
learned from earlier coding-agent conversations.

Before starting substantial work, retrieve relevant skills for the current
task when the Chat2Skill scripts are available:

```bash
python3 scripts/retrieve_for_prompt.py "<current task>"
```

Apply any returned prompt snippet when it is relevant. Do not apply unrelated
skills just because they were retrieved.

After a session with user corrections, preferences, or reusable constraints,
update Chat2Skill from the newest supported transcript:

```bash
python3 scripts/update_from_transcript.py --latest
```

Generated skills live under `~/.chat2skill/skills/`. Do not edit generated
skills by hand unless the user explicitly asks.
