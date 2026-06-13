# Chat2Skill

Use Chat2Skill to retrieve and update reusable skills learned from previous
coding-agent conversations.

Before substantial work, run:

```bash
python3 scripts/retrieve_for_prompt.py "<current task>"
```

Apply relevant returned skills. Ignore unrelated results.

After a useful session, correction, or reusable preference, run:

```bash
python3 scripts/update_from_transcript.py --latest
```

Generated skills are stored under `~/.chat2skill/skills/`; do not edit them
manually unless the user explicitly asks.
