import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from chat2skill.hookio import transcript_path_from_input
from chat2skill.transcripts import find_latest_session
import process_stop_queue


class TranscriptRoutingTests(unittest.TestCase):
    def test_codex_fallback_selects_latest_session_for_same_project_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            correct_old = self._codex_session(home, "session-a-old", "/repo/a", 10)
            correct_new = self._codex_session(home, "session-a-new", "/repo/a", 20)
            self._codex_session(home, "session-b-newest", "/repo/b", 30)

            with patch("chat2skill.transcripts.Path.home", return_value=home):
                selected = find_latest_session("/repo/a")

            self.assertNotEqual(selected, correct_old)
            self.assertEqual(selected, correct_new)

    def test_codex_fallback_fails_closed_when_project_has_no_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._codex_session(home, "session-b", "/repo/b", 10)

            with patch("chat2skill.transcripts.Path.home", return_value=home):
                selected = find_latest_session("/repo/a")

            self.assertIsNone(selected)

    def test_hook_session_id_selects_matching_codex_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            expected = self._codex_session(home, "thread-123", "/repo/a", 10)
            self._codex_session(home, "thread-456", "/repo/a", 20)

            with patch("chat2skill.transcripts.Path.home", return_value=home):
                selected = transcript_path_from_input(
                    {"cwd": "/repo/a", "thread_id": "thread-123"}
                )

            self.assertEqual(selected, expected)

    def test_hook_rejects_explicit_codex_transcript_from_another_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            wrong = self._codex_session(home, "thread-b", "/repo/b", 20)

            with patch("chat2skill.transcripts.Path.home", return_value=home):
                selected = transcript_path_from_input(
                    {"cwd": "/repo/a", "transcript_path": str(wrong)}
                )

            self.assertIsNone(selected)

    def test_claude_fallback_uses_project_specific_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            expected = self._claude_session(home, "/repo/a", "claude-a", 10)
            self._claude_session(home, "/repo/b", "claude-b", 20)

            with patch("chat2skill.transcripts.Path.home", return_value=home):
                selected = find_latest_session("/repo/a")

            self.assertEqual(selected, expected)

    def test_no_project_preserves_manual_global_latest_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._codex_session(home, "session-a", "/repo/a", 10)
            expected = self._claude_session(home, "/repo/b", "claude-b", 20)

            with patch("chat2skill.transcripts.Path.home", return_value=home):
                selected = find_latest_session()

            self.assertEqual(selected, expected)

    def test_worker_skips_queued_transcript_from_another_project(self):
        job = {
            "user_id": "user-a",
            "project_dir": "/repo/a",
            "session_file": "/tmp/repo-b-session.jsonl",
        }
        with patch.object(
            process_stop_queue,
            "transcript_matches_project",
            return_value=False,
        ):
            with patch.object(process_stop_queue.runner, "run_extraction") as extraction:
                with patch.object(process_stop_queue, "log_event") as log_event:
                    process_stop_queue.process_job(job, {})

        extraction.assert_not_called()
        log_event.assert_called_once_with(
            "StopWorker.job_skipped",
            user_id="user-a",
            session_file="/tmp/repo-b-session.jsonl",
            project_dir="/repo/a",
            reason="transcript_project_mismatch",
        )

    @staticmethod
    def _codex_session(
        home: Path,
        session_id: str,
        project_dir: str,
        modified: int,
    ) -> Path:
        path = home / ".codex" / "sessions" / "2026" / f"rollout-{session_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {"session_id": session_id, "cwd": project_dir},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        os.utime(path, (modified, modified))
        return path

    @staticmethod
    def _claude_session(
        home: Path,
        project_dir: str,
        session_id: str,
        modified: int,
    ) -> Path:
        project_slug = str(Path(project_dir).resolve()).replace("/", "-")
        path = home / ".claude" / "projects" / project_slug / f"{session_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        os.utime(path, (modified, modified))
        return path


if __name__ == "__main__":
    unittest.main()
