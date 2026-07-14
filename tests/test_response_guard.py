from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from chat2skill.response_guard import (
    apply_response_guard_mode,
    blocking_stop_hook_supported,
    evaluate_message,
    find_banned_terms,
    reset_guard_state,
    stop_hook_output,
)


STRUCTURED_SOURCE = (
    "---\n"
    "name: deterministic-response-constraint\n"
    "response_guard:\n"
    "  enabled: true\n"
    "  mode: forbidden_terms\n"
    "  forbidden_terms:\n"
    "    - alpha-rule\n"
    "    - beta-rule\n"
    "    - gamma-rule\n"
    "---\n"
)

EVIDENCE_BASED_SOURCE = (
    "---\n"
    "name: deterministic-response-constraint\n"
    "response_guard:\n"
    "  enabled: true\n"
    "  mode: evidence_based_terms\n"
    "  requires_evidence: true\n"
    "  allow_evidence_gap_disclosure: true\n"
    "  strict_terms:\n"
    "    - strict-block\n"
    "  evidence_markers:\n"
    "    - evidence-token\n"
    "  gap_markers:\n"
    "    - gap-token\n"
    "  forbidden_terms:\n"
    "    - strict-block\n"
    "    - context-block\n"
    "---\n"
)


class ResponseGuardTests(unittest.TestCase):
    def test_blocking_stop_hook_is_disabled_for_codex(self):
        self.assertFalse(
            blocking_stop_hook_supported({"CODEX_PLUGIN_ROOT": "/tmp/chat2skill"})
        )
        self.assertFalse(
            blocking_stop_hook_supported(
                {
                    "CLAUDE_PLUGIN_ROOT": (
                        "/Users/test/.codex/plugins/cache/chat2skill/chat2skill/0.1.3"
                    )
                }
            )
        )
        self.assertFalse(
            blocking_stop_hook_supported(
                {"CLAUDE_PLUGIN_ROOT": "/tmp/chat2skill"},
                Path("/Users/test/.codex/plugins/cache/chat2skill/hook.py"),
            )
        )
        self.assertTrue(
            blocking_stop_hook_supported({"CLAUDE_PLUGIN_ROOT": "/tmp/chat2skill"})
        )

    def test_blocks_terms_from_structured_guard_frontmatter(self):
        result = evaluate_message(
            "The response contains beta-rule.",
            sources=[STRUCTURED_SOURCE + '\nExamples may mention "payload", "EventPayload", and "scope".'],
            user_id="u",
        )

        self.assertTrue(result.blocked)
        self.assertEqual(result.terms, ("beta-rule",))
        payload = json.loads(stop_hook_output(result))
        self.assertEqual(payload["decision"], "block")
        self.assertIn("active project skill rules", payload["reason"])

    def test_allows_message_when_no_constraint_source_exists(self):
        result = evaluate_message(
            "The response contains beta-rule.",
            sources=["plain project summary"],
            user_id="u",
        )

        self.assertFalse(result.blocked)

    def test_does_not_infer_terms_from_examples_or_code_words(self):
        result = evaluate_message(
            "The response discusses payload and EventPayload.",
            sources=[
                (
                    "---\nname: no-hedging-terms\n---\n\n"
                    '- Wrong: "The scope is derived from the payload"\n'
                    '- Right: "The payload has a scope_hint field"\n'
                    "- Mention `EventPayload` only as a code identifier.\n"
                )
            ],
            user_id="u",
        )

        self.assertFalse(result.blocked)

    def test_ignores_code_blocks_and_inline_code(self):
        hits = find_banned_terms(
            "Example: `guarded value`\n```\nguarded value\n```",
            ["guarded"],
        )

        self.assertEqual(hits, [])

    def test_latin_terms_use_word_boundaries(self):
        hits = find_banned_terms("terminal is clean. term is present.", ["term"])

        self.assertEqual(hits, ["term"])

    def test_does_not_extract_short_positive_examples(self):
        result = evaluate_message(
            "①②③",
            sources=[
                "positive examples: “①”, “②”, “③”"
            ],
            user_id="u",
        )

        self.assertFalse(result.blocked)

    def test_evidence_based_mode_allows_gap_with_validation_action(self):
        result = evaluate_message(
            "gap-token context-block evidence-token.",
            sources=[EVIDENCE_BASED_SOURCE],
            user_id="u",
        )

        self.assertFalse(result.blocked)

    def test_evidence_based_mode_blocks_speculative_gap_claims(self):
        result = evaluate_message(
            "gap-token strict-block evidence-token.",
            sources=[EVIDENCE_BASED_SOURCE],
            user_id="u",
        )

        self.assertTrue(result.blocked)
        self.assertEqual(result.terms, ("strict-block",))

    def test_evidence_based_mode_requires_policy_markers_from_frontmatter(self):
        source = (
            "---\n"
            "name: deterministic-response-constraint\n"
            "response_guard:\n"
            "  enabled: true\n"
            "  mode: evidence_based_terms\n"
            "  allow_evidence_gap_disclosure: true\n"
            "  forbidden_terms:\n"
            "    - context-block\n"
            "---\n"
        )
        result = evaluate_message(
            "gap-token context-block evidence-token.",
            sources=[source],
            user_id="u",
        )

        self.assertTrue(result.blocked)
        self.assertEqual(result.terms, ("context-block",))

    def test_block_once_mode_suppresses_repeated_hits_until_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "response-guard-state.json"
            result = evaluate_message(
                "The response contains beta-rule.",
                sources=[STRUCTURED_SOURCE],
                user_id="project-user",
            )

            with patch("chat2skill.response_guard.GUARD_STATE_PATH", state_path):
                first = apply_response_guard_mode(result, mode="block-once")
                second = apply_response_guard_mode(result, mode="block-once")
                reset_guard_state("project-user")
                third = apply_response_guard_mode(result, mode="block-once")

        self.assertTrue(first.blocked)
        self.assertFalse(second.blocked)
        self.assertTrue(second.suppressed)
        self.assertTrue(third.blocked)

    def test_adaptive_mode_reduces_block_frequency(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "response-guard-state.json"
            result = evaluate_message(
                "The response contains beta-rule.",
                sources=[
                    (
                        "---\n"
                        "name: deterministic-response-constraint\n"
                        "response_guard:\n"
                        "  enabled: true\n"
                        "  mode: forbidden_terms\n"
                        "  forbidden_terms:\n"
                        "    - beta-rule\n"
                        "---\n"
                    )
                ],
                user_id="project-user",
            )

            with patch("chat2skill.response_guard.GUARD_STATE_PATH", state_path):
                first = apply_response_guard_mode(result, mode="adaptive")
                second = apply_response_guard_mode(result, mode="adaptive")
                third = apply_response_guard_mode(result, mode="adaptive")
                fourth = apply_response_guard_mode(result, mode="adaptive")

        self.assertTrue(first.blocked)
        self.assertFalse(second.blocked)
        self.assertTrue(second.suppressed)
        self.assertEqual(second.suppression_reason, "adaptive_cooldown")
        self.assertTrue(third.blocked)
        self.assertFalse(fourth.blocked)

    def test_strict_mode_keeps_blocking_repeated_hits(self):
        result = evaluate_message(
            "The response contains beta-rule.",
            sources=[STRUCTURED_SOURCE],
            user_id="project-user",
        )

        first = apply_response_guard_mode(result, mode="strict")
        second = apply_response_guard_mode(result, mode="strict")

        self.assertTrue(first.blocked)
        self.assertTrue(second.blocked)


if __name__ == "__main__":
    unittest.main()
