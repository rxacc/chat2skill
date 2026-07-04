from __future__ import annotations

import json
import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from chat2skill import memory_client, runner
from chat2skill.context_store import apply_memory_result, load_context, save_context
from chat2skill.embedding_client import LocalTransformersEmbeddingClient
from chat2skill.i18n import LANGUAGES
from chat2skill.models import MemoryItem, Skill
from chat2skill.recall_policy import should_synthesize_recall
from chat2skill.retrieval import MemoryRetriever
import hook_user_prompt_submit
import process_stop_queue


def _config() -> dict:
    return {
        "api_url": "https://api.example.test",
        "memory": {
            "target_model": "generic",
            "token_budget": 4000,
            "agent_id": "chat2skill-test",
        },
    }


class MemoryClientTests(unittest.TestCase):
    def test_llm_payload_includes_separate_embedding_config(self):
        payload = memory_client.llm_payload(
            {
                "llm": {
                    "api_key": "chat-key-123",
                    "base_url": "https://chat.example/v1",
                    "model": "gpt-test",
                },
                "embedding": {
                    "api_key": "embed-key-123",
                    "base_url": "http://127.0.0.1:5831/v1",
                    "model": "local-embed",
                },
            }
        )

        self.assertEqual(payload["api_key"], "chat-key-123")
        self.assertEqual(payload["embedding_api_key"], "embed-key-123")
        self.assertEqual(payload["embedding_base_url"], "http://127.0.0.1:5831/v1")
        self.assertEqual(payload["embedding_model"], "local-embed")

    def test_local_transformers_embedding_stays_local(self):
        payload = memory_client.llm_payload(
            {
                "llm": {
                    "api_key": "chat-key-123",
                    "base_url": "https://chat.example/v1",
                    "model": "gpt-test",
                },
                "embedding": {
                    "provider": "local_transformers",
                    "model": "Snowflake/snowflake-arctic-embed-xs",
                    "dimensions": 384,
                },
            }
        )

        self.assertEqual(payload["api_key"], "chat-key-123")
        self.assertNotIn("embedding_api_key", payload)
        self.assertIsInstance(
            memory_client._build_embedding_client(  # pylint: disable=protected-access
                {
                    "embedding": {
                        "provider": "local_transformers",
                        "model": "Snowflake/snowflake-arctic-embed-xs",
                        "dimensions": 384,
                    }
                }
            ),
            LocalTransformersEmbeddingClient,
        )

    def test_local_transformers_client_calls_node_helper(self):
        completed = type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps({"vectors": [[0.1, 0.2], [0.3, 0.4]]}),
                "stderr": "",
            },
        )()
        with patch("chat2skill.embedding_client.subprocess.run", return_value=completed) as run:
            client = LocalTransformersEmbeddingClient(
                model="Snowflake/snowflake-arctic-embed-xs",
                dimensions=384,
                node_path="/usr/bin/node",
            )
            vectors = client.embed_many(["one", "two"])

        self.assertEqual(vectors, [[0.1, 0.2], [0.3, 0.4]])
        self.assertEqual(run.call_args.args[0][0], "/usr/bin/node")
        body = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(body["model"], "Snowflake/snowflake-arctic-embed-xs")
        self.assertEqual(body["dimensions"], 384)

    def test_memory_retriever_prefers_embedding_match(self):
        class FakeEmbedder:
            def embed(self, text, model=None):
                return [1.0, 0.0]

        memories = [
            {
                "id": "wrong-lexical",
                "content": "agentos pending action sms signal",
                "memory_type": "decision",
                "section": "project",
                "salience": 0.9,
                "confidence": 0.9,
                "embedding": [0.0, 1.0],
            },
            {
                "id": "right-semantic",
                "content": "evolution service has been shut down and no code changes are needed.",
                "memory_type": "fact",
                "section": "project",
                "salience": 0.5,
                "confidence": 0.8,
                "embedding": [0.99, 0.01],
            },
        ]

        retrieved = MemoryRetriever(embedding_client=FakeEmbedder()).retrieve(
            "agentos evolution 服务关闭 不用改代码",
            memories,
            top_k=2,
        )

        self.assertEqual(retrieved[0].memory["id"], "right-semantic")

    def test_context_memories_get_local_embeddings(self):
        class FakeEmbedder:
            def embed_many(self, texts, model=None):
                return [[0.1, 0.2] for _ in texts]

        context = {
            "memories": [
                {
                    "id": "m1",
                    "content": "evolution service is shut down",
                    "memory_type": "fact",
                    "section": "project",
                    "embedding": [],
                }
            ]
        }

        memory_client._embed_context_memories(  # pylint: disable=protected-access
            context,
            FakeEmbedder(),
            "Snowflake/snowflake-arctic-embed-xs",
        )

        self.assertEqual(context["memories"][0]["embedding"], [0.1, 0.2])

    def test_recall_policy_uses_profile_markers(self):
        self.assertTrue(should_synthesize_recall("pending action 之前我们讨论过什么"))
        self.assertTrue(should_synthesize_recall("what did we decide last time about pending action"))
        self.assertFalse(should_synthesize_recall("implement pending action"))

    def test_all_language_profiles_define_recall_markers(self):
        for code, profile in LANGUAGES.items():
            with self.subTest(code=code):
                self.assertGreater(len(profile.recall_direct_markers), 0)
                self.assertGreater(len(profile.recall_history_markers), 0)
                self.assertGreater(len(profile.recall_topic_markers), 0)

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
            self.assertEqual(context["last_materialization"]["skills_included"], ["ec2-deploy-check"])
            self.assertIn("Project deploys on EC2.", context["last_materialization"]["rendered_prompt"])
            conn = sqlite3.connect(str(db_path))
            row = conn.execute(
                """
                SELECT skills_included, rendered_prompt, token_count
                FROM memory_materializations
                WHERE materialization_id = ?
                """,
                (result["materialization_id"],),
            ).fetchone()
            conn.close()
            self.assertEqual(json.loads(row[0]), ["ec2-deploy-check"])
            self.assertIn("Project deploys on EC2.", row[1])
            self.assertGreater(row[2], 0)
            self.assertTrue(db_path.exists())
            self.assertFalse(list(context_dir.rglob("*.json")))

    def test_history_prompt_calls_recall_synthesis_and_prepends_summary(self):
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
                                "core_memory": "Project discusses pending action.",
                                "memories": [
                                    {
                                        "id": "m1",
                                        "content": "PendingAction is an independent subsystem.",
                                        "memory_type": "decision",
                                        "section": "pending_action",
                                        "confidence": 0.9,
                                        "salience": 0.9,
                                    }
                                ],
                                "schemas": [],
                                "recent_raw_hashes": [],
                            },
                        )
                        with patch.object(
                            memory_client.api_client,
                            "unified_recall_synthesize",
                            return_value={
                                "llm_used": False,
                                "recall_summary": "## 回忆整合\n- PendingAction 是独立子系统。",
                                "memories_included": ["m1"],
                                "skills_included": [],
                                "token_count": 20,
                            },
                        ) as cloud_recall:
                            result = memory_client.materialize_for_prompt(
                                _config(), "/repo/project", "pending action 之前我们讨论过什么", "user-1"
                            )

            cloud_recall.assert_called_once()
            self.assertIn("Chat2Skill Recall Summary", result["rendered_text"])
            self.assertIn("PendingAction 是独立子系统", result["rendered_text"])
            self.assertEqual(result["recall_synthesis"]["memories_included"], ["m1"])

    def test_non_history_prompt_skips_recall_synthesis(self):
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
                                "core_memory": "",
                                "memories": [],
                                "schemas": [],
                                "recent_raw_hashes": [],
                            },
                        )
                        with patch.object(memory_client.api_client, "unified_recall_synthesize") as cloud_recall:
                            result = memory_client.materialize_for_prompt(
                                _config(), "/repo/project", "implement pending action", "user-1"
                            )

            cloud_recall.assert_not_called()
            self.assertNotIn("recall_synthesis", result)

    def test_user_prompt_hook_records_final_injected_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "c2s.db"
            skill_dir = Path(tmp) / "skills"
            with patch.object(memory_client.storage, "DB_PATH", db_path):
                with patch.object(memory_client.storage, "SKILL_DIR", skill_dir):
                    memory_client.storage.init_db()
                    result = {
                        "materialization_id": "mat-hook",
                        "rendered_text": "## Relevant Project Memories\n- [fact/project] Hook memory.",
                        "token_count": 14,
                        "memory": {"memories_included": ["m-hook"], "coverage_score": 1.0},
                        "skills": {"skills_included": ["hook-skill"]},
                    }
                    with patch.object(hook_user_prompt_submit, "materialize_for_prompt", return_value=result):
                        with redirect_stdout(io.StringIO()):
                            code = hook_user_prompt_submit.inject_memory_context(
                                _config(), "/repo/project", "user-1", "current prompt"
                            )
                    conn = sqlite3.connect(str(db_path))
                    row = conn.execute(
                        """
                        SELECT rendered_prompt, skills_included, memories_included, query, token_count
                        FROM memory_materializations
                        WHERE materialization_id = 'mat-hook'
                        """
                    ).fetchone()
                    conn.close()

        self.assertEqual(code, 0)
        self.assertIn("## Chat2Skill Memory and Skills", row[0])
        self.assertIn("Materialization ID: mat-hook", row[0])
        self.assertEqual(json.loads(row[1]), ["hook-skill"])
        self.assertEqual(json.loads(row[2]), ["m-hook"])
        self.assertEqual(row[3], "current prompt")
        self.assertEqual(row[4], 14)

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
            activity_row = conn.execute(
                """
                SELECT raw_input, raw_messages, memory_ids_produced
                FROM memory_activity
                """
            ).fetchone()
            conn.close()
            self.assertEqual(activity_count, 1)
            self.assertIn("remember EC2 deploy", activity_row[0])
            self.assertEqual(json.loads(activity_row[1])[0]["content"], "remember EC2 deploy")
            self.assertEqual(json.loads(activity_row[2]), ["b1"])

    def test_materialize_includes_similar_prior_tasks_from_activity_embeddings(self):
        class FakeEmbedder:
            def embed(self, text, model=None):
                return [1.0, 0.0]

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
                                "core_memory": "",
                                "memories": [
                                    {
                                        "id": "m-worked",
                                        "content": "Prior EC2 rollout used the approval path.",
                                        "memory_type": "decision",
                                        "section": "deployment",
                                        "confidence": 0.9,
                                        "salience": 0.9,
                                        "embedding": [1.0, 0.0],
                                    }
                                ],
                                "schemas": [],
                                "recent_raw_hashes": [],
                            },
                        )
                        project_key = memory_client.context_key("/repo/project")
                        memory_client.storage.record_project_memory_activity(
                            "user-1",
                            project_key,
                            "session-worked",
                            {"raw_input_hash": "raw-worked", "delta_batch": {}},
                            raw_input="How did we handle the prior EC2 rollout?",
                            raw_messages=[
                                {
                                    "role": "user",
                                    "content": "How did we handle the prior EC2 rollout?",
                                }
                            ],
                            input_embedding=[1.0, 0.0],
                            memory_ids_produced=["m-worked"],
                        )
                        with patch.object(memory_client, "_build_embedding_client", return_value=FakeEmbedder()):
                            result = memory_client.materialize_for_prompt(
                                _config(),
                                "/repo/project",
                                "current EC2 rollout",
                                "user-1",
                            )

            self.assertIn("Similar Prior Tasks", result["rendered_text"])
            self.assertIn("How did we handle the prior EC2 rollout?", result["rendered_text"])
            self.assertIn("Prior EC2 rollout used the approval path.", result["rendered_text"])
            self.assertEqual(result["memory"]["activities_included"], ["1"])

    def test_materialization_outcome_reconsolidates_included_memories(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "c2s.db"
            skill_dir = Path(tmp) / "skills"
            with patch.object(memory_client.storage, "DB_PATH", db_path):
                with patch.object(memory_client.storage, "SKILL_DIR", skill_dir):
                    memory_client.storage.init_db()
                    save_context(
                        "/repo/project",
                        "user-1",
                        {
                            "core_memory": "",
                            "memories": [
                                {
                                    "id": "m1",
                                    "content": "Use approval path.",
                                    "memory_type": "decision",
                                    "section": "project",
                                    "salience": 0.5,
                                    "confidence": 0.9,
                                }
                            ],
                            "schemas": [],
                            "recent_raw_hashes": [],
                        },
                    )
                    project_key = memory_client.context_key("/repo/project")
                    memory_client.storage.save_project_memory_materialization(
                        "user-1",
                        project_key,
                        {
                            "materialization_id": "mat-1",
                            "memories_included": ["m1"],
                            "skills_included": [],
                            "query": "approval",
                            "rendered_prompt": "Use approval path.",
                        },
                    )
                    result = memory_client.storage.record_materialization_outcome(
                        "user-1",
                        "mat-1",
                        "success",
                        feedback={"note": "useful"},
                    )
                    conn = sqlite3.connect(str(db_path))
                    row = conn.execute(
                        """
                        SELECT recall_count, hit_count, miss_count, salience
                        FROM memory_items
                        WHERE id = 'm1'
                        """
                    ).fetchone()
                    mat = conn.execute(
                        """
                        SELECT outcome, feedback, reconsolidated_at
                        FROM memory_materializations
                        WHERE materialization_id = 'mat-1'
                        """
                    ).fetchone()
                    conn.close()

            self.assertEqual(result["memories_reconsolidated"], 1)
            self.assertEqual(row[0], 1)
            self.assertEqual(row[1], 1)
            self.assertEqual(row[2], 0)
            self.assertGreater(row[3], 0.5)
            self.assertEqual(mat[0], "success")
            self.assertEqual(json.loads(mat[1]), {"note": "useful"})
            self.assertTrue(mat[2])

    def test_reextract_project_memory_dry_run_uses_stored_raw_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "c2s.db"
            skill_dir = Path(tmp) / "skills"
            with patch.object(memory_client.storage, "DB_PATH", db_path):
                with patch.object(memory_client.storage, "SKILL_DIR", skill_dir):
                    memory_client.storage.init_db()
                    save_context(
                        "/repo/project",
                        "user-1",
                        {
                            "core_memory": "",
                            "memories": [],
                            "schemas": [],
                            "recent_raw_hashes": [],
                        },
                    )
                    project_key = memory_client.context_key("/repo/project")
                    memory_client.storage.record_project_memory_activity(
                        "user-1",
                        project_key,
                        "session-raw",
                        {"raw_input_hash": "raw-1", "delta_batch": {}},
                        raw_input="remember the approval decision",
                        raw_messages=[{"role": "user", "content": "remember the approval decision"}],
                    )
                    with patch.object(memory_client.api_client, "unified_learn") as learn:
                        result = memory_client.re_extract_project_memory(
                            _config(),
                            "/repo/project",
                            "user-1",
                            dry_run=True,
                        )

            learn.assert_not_called()
            self.assertEqual(result["status"], "preview")
            self.assertEqual(result["activities_found"], 1)
            self.assertEqual(result["activities"][0]["session_id"], "session-raw")

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
            assert synced is not None
            assert saved is not None
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
            assert saved is not None
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
                    sources = runner.storage.load_project_skill_sources("user-1")

            assert saved is not None
            self.assertEqual(len(skill_payload["content"]), 2215)
            self.assertTrue(skill_payload["content"].endswith("\n...[truncated]"))
            self.assertEqual(skill_payload["embedding_vector"], [])
            self.assertEqual(skill_payload["memory_items"][0]["item_type"], "constraint")
            self.assertEqual(skill_payload["memory_items"][0]["title"], "Keep rollback")
            self.assertEqual(saved["source_memory_count"], 2)
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0]["skill_name"], "deploy-process")
            self.assertEqual(sources[0]["project_skill_version"], saved["version"])
            self.assertEqual(sources[0]["source_memory_count"], 2)

    def test_stop_worker_rebuilds_project_skill_after_memory_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session.jsonl"
            session.write_text("{}", encoding="utf-8")
            job = {
                "user_id": "user-1",
                "session_file": str(session),
                "project_dir": "/repo/project",
            }
            config = _config()

            with patch.object(
                process_stop_queue.runner,
                "run_extraction",
                return_value={"status": "memory_saved"},
            ):
                with patch.object(process_stop_queue.runner, "run_maintenance") as maintenance:
                    with patch.object(
                        process_stop_queue.runner,
                        "rebuild_project_skill",
                        return_value=Path(tmp) / "PROJECT_SKILL.md",
                    ) as rebuild:
                        with patch.object(
                            process_stop_queue,
                            "parse_transcript",
                            return_value=[{"role": "user", "content": "hello"}],
                        ):
                            with patch.object(process_stop_queue, "log_event"):
                                process_stop_queue.process_job(job, config)

            maintenance.assert_not_called()
            rebuild.assert_called_once_with(
                "user-1",
                config,
                [{"role": "user", "content": "hello"}],
            )

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
