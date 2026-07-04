import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PluginManifestTests(unittest.TestCase):
    def test_codex_plugin_ships_lifecycle_hooks(self):
        plugin_path = ROOT / ".codex-plugin" / "plugin.json"
        hooks_path = ROOT / ".codex-plugin" / "hooks.json"

        self.assertTrue(plugin_path.exists())
        self.assertTrue(hooks_path.exists())

        plugin = json.loads(plugin_path.read_text(encoding="utf-8"))
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))

        self.assertEqual(plugin["name"], "chat2skill")
        self.assertIn("Lifecycle hooks", plugin["interface"]["capabilities"])
        self.assertIn("UserPromptSubmit", hooks["hooks"])
        self.assertIn("Stop", hooks["hooks"])

        commands = [
            hook["command"]
            for event in hooks["hooks"].values()
            for group in event
            for hook in group["hooks"]
        ]
        self.assertTrue(all("${CODEX_PLUGIN_ROOT}" in command for command in commands))
        self.assertTrue(all("scripts/chat2skill_hook.py" in command for command in commands))

    def test_agent_plugin_hook_files_exist(self):
        for path in (
            ".claude-plugin/hooks.json",
            ".codex-plugin/hooks.json",
            ".cursor-plugin/hooks.json",
        ):
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).exists())

    def test_claude_plugin_ships_lifecycle_hooks(self):
        plugin_path = ROOT / ".claude-plugin" / "plugin.json"
        marketplace_path = ROOT / ".claude-plugin" / "marketplace.json"
        hooks_path = ROOT / ".claude-plugin" / "hooks.json"

        plugin = json.loads(plugin_path.read_text(encoding="utf-8"))
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))

        self.assertEqual(plugin["name"], "chat2skill")
        self.assertEqual(plugin["version"], marketplace["metadata"]["version"])
        self.assertEqual(plugin["version"], marketplace["plugins"][0]["version"])
        self.assertIn("UserPromptSubmit", hooks["hooks"])
        self.assertIn("Stop", hooks["hooks"])

        commands = [
            hook["command"]
            for event in hooks["hooks"].values()
            for group in event
            for hook in group["hooks"]
        ]
        self.assertTrue(all("${CLAUDE_PLUGIN_ROOT}" in command for command in commands))
        self.assertTrue(all("scripts/chat2skill_hook.py" in command for command in commands))


if __name__ == "__main__":
    unittest.main()
