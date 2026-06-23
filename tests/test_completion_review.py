from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from chat2skill.completion_review import evaluate_completion, latest_user_prompt, stop_hook_output
from hook_user_prompt_submit import format_detailed_skills


class CompletionReviewTests(unittest.TestCase):
    def test_blocks_completion_claim_without_evidence(self):
        result = evaluate_completion(
            prompt="修复这个 bug，同时检查前端后端不要遗漏",
            assistant_message="已修复这个问题。",
            mode="strict",
        )

        self.assertTrue(result.blocked)
        payload = json.loads(stop_hook_output(result))
        self.assertEqual(payload["decision"], "block")
        self.assertIn("completion review", payload["reason"])

    def test_allows_completion_with_evidence_and_scope(self):
        result = evaluate_completion(
            prompt="修复这个 bug，同时检查前端后端不要遗漏",
            assistant_message=(
                "已修复。覆盖范围：前端和后端路径已核对。"
                "验证：运行 tests passed。未验证：无。"
            ),
            mode="strict",
        )

        self.assertFalse(result.blocked)

    def test_ignores_question_only_prompt(self):
        result = evaluate_completion(
            prompt="这个功能的设计是什么？",
            assistant_message="这个功能由入口层和执行层组成。",
            mode="strict",
        )

        self.assertFalse(result.blocked)

    def test_warn_only_does_not_block(self):
        result = evaluate_completion(
            prompt="修复这个 bug，同时检查前端后端不要遗漏",
            assistant_message="已修复这个问题。",
            mode="warn-only",
        )

        self.assertFalse(result.blocked)
        self.assertGreaterEqual(len(result.reasons), 1)

    def test_extracts_latest_real_user_prompt_from_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "response_item",
                                "payload": {
                                    "role": "user",
                                    "content": [{"text": "修复 A 并检查 B"}],
                                },
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "type": "response_item",
                                "payload": {
                                    "role": "assistant",
                                    "content": [{"text": "处理中"}],
                                },
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "type": "response_item",
                                "payload": {
                                    "role": "user",
                                    "content": [{"text": "<hook_prompt>ignore</hook_prompt>"}],
                                },
                            },
                            ensure_ascii=False,
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            prompt = latest_user_prompt({"transcript_path": str(transcript)})

        self.assertEqual(prompt, "修复 A 并检查 B")

    def test_formats_detailed_skills_with_content(self):
        class Item:
            score = 0.5

            class skill:
                name = "sample-skill"
                description = "Sample description"
                content = "---\nname: sample-skill\n---\n\n## Checklist\n- one"

        text = format_detailed_skills([Item()])

        self.assertIn("sample-skill", text)
        self.assertIn("Checklist", text)


if __name__ == "__main__":
    unittest.main()
