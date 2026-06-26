from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from chat2skill import memory_client
from chat2skill.context_store import apply_memory_result, load_context


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
    def test_materialize_calls_unified_retrieve_and_records_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            context_dir = Path(tmp) / "contexts"
            calls = []

            def fake_retrieve(api_url, payload):
                calls.append((api_url, payload))
                self.assertEqual(api_url, "https://api.example.test")
                self.assertEqual(payload["query"], "current task")
                self.assertIn("existing_memory", payload)
                self.assertIn("existing_skills", payload)
                return {
                    "rendered_text": "## Project Memory\nmemory",
                    "materialization_id": "mat-1",
                    "token_count": 10,
                    "memory": {
                        "bullets_included": ["b1"],
                        "coverage_score": 1.0,
                    },
                    "skills": {"skills_included": []},
                }

            with patch("chat2skill.context_store.CONTEXTS_DIR", context_dir):
                with patch.object(memory_client.api_client, "unified_retrieve", side_effect=fake_retrieve):
                    result = memory_client.materialize_for_prompt(
                        _config(), "/repo/project", "current task", "user-1"
                    )
                context = load_context("/repo/project", "user-1")

            self.assertEqual(result["materialization_id"], "mat-1")
            self.assertEqual(context["last_materialization"]["materialization_id"], "mat-1")
            self.assertEqual(context["last_materialization"]["bullets_included"], ["b1"])
            self.assertEqual(calls[0][1]["existing_memory"]["bullets"], [])

    def test_commit_transcript_calls_unified_learn_and_applies_memory_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            context_dir = Path(tmp) / "contexts"
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
                                    "op_type": "add_bullet",
                                    "target_id": "b1",
                                    "section": "deployment",
                                    "content": "EC2 deploy is durable memory.",
                                    "bullet_type": "fact",
                                    "confidence": 0.8,
                                    "previous_state": {},
                                }
                            ],
                        },
                        "core_memory_update": "Project uses EC2 deploy.",
                        "raw_input_hash": "hash-1",
                        "bullets_added": 1,
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
                with patch.object(memory_client.api_client, "unified_learn", side_effect=fake_learn):
                    result = memory_client.commit_transcript(
                        transcript, "user-1", _config(), project_dir="/repo/project"
                    )
                context = load_context("/repo/project", "user-1")

            self.assertEqual(result["status"], "memory_saved")
            self.assertEqual(result["memory"]["bullets_added"], 1)
            self.assertEqual(context["core_memory"], "Project uses EC2 deploy.")
            self.assertEqual(context["bullets"][0]["id"], "b1")
            self.assertEqual(context["recent_raw_hashes"], ["hash-1"])
            self.assertEqual(calls[0][1]["messages"][0]["content"], "remember EC2 deploy")

    def test_apply_memory_result_updates_existing_bullet(self):
        context = {
            "core_memory": "",
            "bullets": [{"id": "b1", "content": "old", "confidence": 0.3}],
            "schemas": [],
            "recent_raw_hashes": [],
        }
        updated = apply_memory_result(
            context,
            {
                "delta_batch": {
                    "operations": [
                        {
                            "op_type": "update_bullet",
                            "target_id": "b1",
                            "content": "new",
                            "confidence": 0.9,
                        }
                    ]
                }
            },
        )
        self.assertEqual(updated["bullets"][0]["content"], "new")
        self.assertEqual(updated["bullets"][0]["confidence"], 0.9)


if __name__ == "__main__":
    unittest.main()
