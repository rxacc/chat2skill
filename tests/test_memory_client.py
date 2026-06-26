from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from chat2skill import memory_client, runner
from chat2skill.context_store import apply_memory_result, load_context, save_context
from chat2skill.models import MemoryItem, Skill


def _config() -> dict:
    return {
        "backend": "memory",
        "api_url": "https://api.example.test",
        "memory": {
            "target_model": "generic",
            "token_budget": 4000,
            "agent_id": "chat2skill-test",
        },
    }


class MemoryClientTests(unittest.TestCase):
    def test_materialize_uses_local_context_and_records_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            context_dir = Path(tmp) / "contexts"
            db_path = Path(tmp) / "c2s.db"
            skill_dir = Path(tmp) / "skills"

            with patch("chat2skill.context_store.CONTEXTS_DIR", context_dir):
                with patch.object(memory_client.storage, "DB_PATH", db_path):
                    with patch.object(memory_client.storage, "SKILL_DIR", skill_dir):
                        memory_client.storage.init_db()
                        save_context(
                            "/repo/project",
                            "user-1",
                            {
                                "core_memory": "Project deploys on EC2.",
                                "memories": [
                                    {
                                        "id": "b1",
                                        "content": "EC2 deploy uses the durable rollout path.",
                                        "memory_type": "procedure",
                                        "section": "deployment",
                                        "confidence": 0.9,
                                        "salience": 0.9,
                                    }
                                ],
                                "schemas": [],
                                "recent_raw_hashes": [],
                            },
                        )
                        memory_client.storage.save_skill(
                            Skill(
                                name="ec2-deploy-check",
                                description="Use the EC2 deploy checklist.",
                                content="Check EC2 rollout before deploy.",
                                skill_type="procedure",
                                status="active",
                                confidence=0.8,
                            ),
                            user_id="user-1",
                        )
                        with patch.object(memory_client.api_client, "unified_retrieve") as cloud_retrieve:
                            result = memory_client.materialize_for_prompt(
                                _config(), "/repo/project", "current EC2 deploy task", "user-1"
                            )
                            cloud_retrieve.assert_not_called()
                        context = load_context("/repo/project", "user-1")

            self.assertIn("Project deploys on EC2.", result["rendered_text"])
            self.assertIn("ec2-deploy-check", result["rendered_text"])
            self.assertEqual(context["last_materialization"]["materialization_id"], result["materialization_id"])
            self.assertEqual(context["last_materialization"]["memories_included"], ["b1"])
            self.assertTrue(db_path.exists())
            self.assertFalse(list(context_dir.rglob("*.json")))

    def test_commit_transcript_calls_unified_learn_and_applies_memory_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            context_dir = Path(tmp) / "contexts"
            db_path = Path(tmp) / "c2s.db"
            skill_dir = Path(tmp) / "skills"
            transcript = Path(tmp) / "session.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "response_item",
                                "payload": {"role": "user", "content": "remember EC2 deploy"},
                            }
                        ),
                        json.dumps(
                            {
                                "type": "response_item",
                                "payload": {"role": "assistant", "content": "done"},
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            calls = []

            def fake_learn(api_url, payload):
                calls.append((api_url, payload))
                self.assertEqual(api_url, "https://api.example.test")
                self.assertIn("existing_memory", payload)
                self.assertIn("existing_skills", payload)
                return {
                    "session_id": "session",
                    "llm_used": False,
                    "memory": {
                        "delta_batch": {
                            "id": "delta-1",
                            "trigger": "commit",
                            "operations": [
                                {
                                    "op_type": "add_memory",
                                    "target_id": "b1",
                                    "section": "deployment",
                                    "content": "EC2 deploy is durable memory.",
                                    "memory_type": "fact",
                                    "confidence": 0.8,
                                    "previous_state": {},
                                }
                            ],
                        },
                        "core_memory_update": "Project uses EC2 deploy.",
                        "raw_input_hash": "hash-1",
                        "memories_added": 1,
                    },
                    "skills": {
                        "skill": None,
                        "updated_profile": {
                            "user_id": "user-1",
                            "preferences": {},
                            "constraints": {},
                            "interaction_count": 0,
                            "last_updated": "now",
                        },
                        "reason": "no_actionable_signals",
                    },
                }

            with patch("chat2skill.context_store.CONTEXTS_DIR", context_dir):
                with patch.object(memory_client.storage, "DB_PATH", db_path):
                    with patch.object(memory_client.storage, "SKILL_DIR", skill_dir):
                        memory_client.storage.init_db()
                        save_context(
                            "/repo/project",
                            "user-1",
                            {
                                "core_memory": "Project uses EC2 deploy.",
                                "memories": [
                                    {
                                        "id": "m1",
                                        "content": "EC2 deploy must keep payloads compact.",
                                        "memory_type": "decision",
                                        "section": "deployment",
                                        "confidence": 0.9,
                                        "salience": 0.9,
                                    }
                                ],
                                "schemas": [],
                                "recent_raw_hashes": [],
                            },
                        )
                        memory_client.storage.save_skill(
                            Skill(
                                name="ec2-deploy-payloads",
                                description="Keep EC2 deploy payloads compact.",
                                content="x" * 5000,
                                skill_type="procedure",
                                status="active",
                                confidence=0.9,
                            ),
                            user_id="user-1",
                        )
                        with patch.object(memory_client.api_client, "unified_learn", side_effect=fake_learn):
                            result = memory_client.commit_transcript(
                                transcript, "user-1", _config(), project_dir="/repo/project"
                            )
                        context = load_context("/repo/project", "user-1")

            self.assertEqual(result["status"], "memory_saved")
            self.assertEqual(result["memory"]["memories_added"], 1)
            self.assertEqual(result["memory"]["context_path"], str(db_path))
            self.assertEqual(context["core_memory"], "Project uses EC2 deploy.")
            self.assertIn("b1", {item["id"] for item in context["memories"]})
            self.assertEqual(context["recent_raw_hashes"], ["hash-1"])
            self.assertEqual(calls[0][1]["messages"][0]["content"], "remember EC2 deploy")
            self.assertEqual([item["id"] for item in calls[0][1]["existing_memory"]["memories"]], ["m1"])
            self.assertEqual(len(calls[0][1]["existing_skills"]), 1)
            self.assertLess(len(calls[0][1]["existing_skills"][0]["content"]), 2500)
            self.assertEqual(calls[0][1]["existing_skills"][0]["embedding_vector"], [])
            self.assertTrue(db_path.exists())
            self.assertFalse(list(context_dir.rglob("*.json")))
            conn = sqlite3.connect(str(db_path))
            activity_count = conn.execute("SELECT COUNT(*) FROM memory_activity").fetchone()[0]
            conn.close()
            self.assertEqual(activity_count, 1)

    def test_init_db_migrates_prefixed_memory_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "c2s.db"
            skill_dir = Path(tmp) / "skills"
            conn = sqlite3.connect(str(db_path))
            conn.executescript(
                """
                CREATE TABLE memory_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    skill_name TEXT,
                    item_type TEXT,
                    title TEXT,
                    description TEXT,
                    content TEXT,
                    evidence TEXT,
                    source_session TEXT,
                    confidence REAL,
                    created_at TEXT
                );
                INSERT INTO memory_items
                (user_id, skill_name, item_type, title, description, content, evidence, source_session, confidence, created_at)
                VALUES ('user-1', 'skill-a', 'constraint', 'title', 'desc', 'content', 'evidence', 'session-a', 0.8, 'now');

                CREATE TABLE c2s_memory_contexts (
                    user_id TEXT NOT NULL,
                    context_key TEXT NOT NULL,
                    project_dir TEXT,
                    core_memory TEXT,
                    recent_raw_hashes TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    PRIMARY KEY (user_id, context_key)
                );
                INSERT INTO c2s_memory_contexts
                VALUES ('user-1', 'project-a', '/repo/project', 'core', '[]', 'now', 'now');

                CREATE TABLE c2s_memory_items (
                    user_id TEXT NOT NULL,
                    context_key TEXT NOT NULL,
                    id TEXT NOT NULL,
                    content TEXT,
                    memory_type TEXT,
                    section TEXT,
                    salience REAL,
                    confidence REAL,
                    embedding TEXT,
                    source_session TEXT,
                    source_agent TEXT,
                    recall_count INTEGER,
                    hit_count INTEGER,
                    miss_count INTEGER,
                    is_active INTEGER,
                    is_archived INTEGER,
                    created_at TEXT,
                    updated_at TEXT,
                    PRIMARY KEY (user_id, context_key, id)
                );
                INSERT INTO c2s_memory_items
                VALUES ('user-1', 'project-a', 'm1', 'memory content', 'fact', 'general', 0.8, 0.9, '[]', 'session-a', 'agent', 0, 0, 0, 1, 0, 'now', 'now');

                CREATE TABLE c2s_memory_schemas (
                    user_id TEXT NOT NULL,
                    context_key TEXT NOT NULL,
                    id TEXT NOT NULL,
                    name TEXT,
                    description TEXT,
                    memory_ids TEXT,
                    created_at TEXT,
                    PRIMARY KEY (user_id, context_key, id)
                );
                CREATE TABLE c2s_memory_materializations (
                    user_id TEXT NOT NULL,
                    context_key TEXT NOT NULL,
                    materialization_id TEXT PRIMARY KEY,
                    memories_included TEXT,
                    query TEXT,
                    outcome TEXT,
                    created_at TEXT
                );
                CREATE TABLE c2s_memory_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    context_key TEXT NOT NULL,
                    session_id TEXT,
                    raw_input_hash TEXT,
                    delta_batch TEXT,
                    created_at TEXT
                );
                """
            )
            conn.commit()
            conn.close()

            with patch.object(memory_client.storage, "DB_PATH", db_path):
                with patch.object(memory_client.storage, "SKILL_DIR", skill_dir):
                    memory_client.storage.init_db()
                    conn = sqlite3.connect(str(db_path))
                    tables = {
                        row[0]
                        for row in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                    skill_count = conn.execute("SELECT COUNT(*) FROM skill_memory_items").fetchone()[0]
                    memory_count = conn.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0]
                    conn.close()

            self.assertNotIn("c2s_memory_items", tables)
            self.assertIn("skill_memory_items", tables)
            self.assertIn("memory_items", tables)
            self.assertEqual(skill_count, 1)
            self.assertEqual(memory_count, 1)

    def test_project_skill_is_saved_and_synced_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "c2s.db"
            skill_dir = Path(tmp) / "skills"
            project_dir = skill_dir / "user-1"
            project_dir.mkdir(parents=True)
            project_file = project_dir / "PROJECT_SKILL.md"
            project_file.write_text(
                "---\nname: project-skill\nlanguage: en\n---\n\n# Project Skill",
                encoding="utf-8",
            )

            with patch.object(memory_client.storage, "DB_PATH", db_path):
                with patch.object(memory_client.storage, "SKILL_DIR", skill_dir):
                    memory_client.storage.init_db()
                    synced = memory_client.storage.load_project_skill("user-1")
                    memory_client.storage.save_project_skill(
                        "user-1",
                        "---\nname: project-skill\nlanguage: en\n---\n\n# Project Skill",
                        file_path=project_file,
                        source_skill_count=3,
                        source_memory_count=2,
                    )
                    saved = memory_client.storage.load_project_skill("user-1")

            self.assertIsNotNone(synced)
            self.assertIn("Project Skill", synced["content"])
            self.assertEqual(synced["language"], "en")
            self.assertEqual(saved["name"], "project-skill")
            self.assertEqual(saved["language"], "en")
            self.assertEqual(saved["source_skill_count"], 3)
            self.assertEqual(saved["source_memory_count"], 2)
            self.assertIn("Project Skill", saved["content"])

    def test_rebuild_project_skill_rejects_incomplete_cloud_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "c2s.db"
            skill_dir = Path(tmp) / "skills"
            project_dir = skill_dir / "user-1"
            project_dir.mkdir(parents=True)
            project_file = project_dir / "PROJECT_SKILL.md"
            original_content = "---\nname: project-skill\nlanguage: en\n---\n\n# Existing"
            project_file.write_text(original_content, encoding="utf-8")

            with patch.object(runner.storage, "DB_PATH", db_path):
                with patch.object(runner.storage, "SKILL_DIR", skill_dir):
                    runner.storage.init_db()
                    runner.storage.save_project_skill(
                        "user-1",
                        original_content,
                        file_path=project_file,
                        source_skill_count=1,
                    )
                    runner.storage.save_skill(
                        Skill(
                            name="active-source",
                            description="Active source skill.",
                            content="Use the existing source skill.",
                            skill_type="procedure",
                            status="active",
                            confidence=0.9,
                        ),
                        user_id="user-1",
                    )
                    with patch.object(
                        runner.api_client,
                        "project_skill",
                        return_value={"content": "---\nname: project-skill\n---\n\n- **"},
                    ):
                        with self.assertRaises(runner.ApiError):
                            runner.rebuild_project_skill(
                                "user-1",
                                {"api_url": "https://api.example.test"},
                            )
                    saved = runner.storage.load_project_skill("user-1")

            self.assertEqual(project_file.read_text(encoding="utf-8"), original_content)
            self.assertEqual(saved["content"], original_content)

    def test_rebuild_project_skill_sends_compact_skill_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "c2s.db"
            skill_dir = Path(tmp) / "skills"

            with patch.object(runner.storage, "DB_PATH", db_path):
                with patch.object(runner.storage, "SKILL_DIR", skill_dir):
                    runner.storage.init_db()
                    runner.storage.save_skill(
                        Skill(
                            name="deploy-process",
                            description="Use the deploy process.",
                            content="x" * 3000,
                            skill_type="procedure",
                            status="active",
                            confidence=0.9,
                        ),
                        user_id="user-1",
                    )
                    runner.storage.save_memory_items(
                        [
                            MemoryItem(
                                item_type="success",
                                title="Low value",
                                description="lower priority",
                                content="low",
                                evidence="low evidence",
                                source_session="s1",
                                confidence=0.9,
                                created_at="2026-01-01T00:00:00",
                            ),
                            MemoryItem(
                                item_type="constraint",
                                title="Keep rollback",
                                description="user requires rollback validation",
                                content="rollback validation is required",
                                evidence="explicit correction",
                                source_session="s2",
                                confidence=0.7,
                                created_at="2026-01-02T00:00:00",
                            ),
                        ],
                        user_id="user-1",
                        skill_name="deploy-process",
                    )
                    with patch.object(
                        runner.api_client,
                        "project_skill",
                        return_value={
                            "content": "---\nname: project-skill\nlanguage: en\n---\n\n# Project Skill"
                        },
                    ) as project_skill:
                        runner.rebuild_project_skill(
                            "user-1",
                            {"api_url": "https://api.example.test"},
                        )
                    payload = project_skill.call_args.args[1]
                    skill_payload = payload["skills"][0]
                    saved = runner.storage.load_project_skill("user-1")

            self.assertEqual(len(skill_payload["content"]), 2215)
            self.assertTrue(skill_payload["content"].endswith("\n...[truncated]"))
            self.assertEqual(skill_payload["embedding_vector"], [])
            self.assertEqual(skill_payload["memory_items"][0]["item_type"], "constraint")
            self.assertEqual(skill_payload["memory_items"][0]["title"], "Keep rollback")
            self.assertEqual(saved["source_memory_count"], 2)

    def test_apply_memory_result_updates_existing_memory(self):
        context = {
            "core_memory": "",
            "memories": [{"id": "b1", "content": "old", "confidence": 0.3}],
            "schemas": [],
            "recent_raw_hashes": [],
        }
        updated = apply_memory_result(
            context,
            {
                "delta_batch": {
                    "operations": [
                        {
                            "op_type": "update_memory",
                            "target_id": "b1",
                            "content": "new",
                            "confidence": 0.9,
                        }
                    ]
                }
            },
        )
        self.assertEqual(updated["memories"][0]["content"], "new")
        self.assertEqual(updated["memories"][0]["confidence"], 0.9)


if __name__ == "__main__":
    unittest.main()
