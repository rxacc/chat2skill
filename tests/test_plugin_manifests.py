import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PluginManifestTests(unittest.TestCase):
    def test_installer_selects_root_manifest_for_target_host(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")

        self.assertIn("install_host_for_target()", installer)
        self.assertIn('*/.codex/*)', installer)
        self.assertIn('*/.claude/*)', installer)
        self.assertIn(
            'write_hooks_json "${target}" "$(install_host_for_target "${target_real}")"',
            installer,
        )

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
        self.assertFalse(any("stop-response-guard" in command for command in commands))

    def test_agent_plugin_hook_files_exist(self):
        for path in (
            ".claude-plugin/hooks.json",
            ".codex-plugin/hooks.json",
            ".cursor-plugin/hooks.json",
            "hooks/hooks.json",
            "hooks/codex-hooks.json",
            "hooks/claude-hooks.json",
        ):
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).exists())

    def test_root_hooks_directory_ships_agent_specific_hooks(self):
        codex_hooks = json.loads((ROOT / ".codex-plugin" / "hooks.json").read_text(encoding="utf-8"))
        claude_hooks = json.loads((ROOT / ".claude-plugin" / "hooks.json").read_text(encoding="utf-8"))
        root_codex_hooks = json.loads((ROOT / "hooks" / "codex-hooks.json").read_text(encoding="utf-8"))
        root_claude_hooks = json.loads((ROOT / "hooks" / "claude-hooks.json").read_text(encoding="utf-8"))
        install_hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))

        self.assertEqual(root_codex_hooks, codex_hooks)
        self.assertEqual(root_claude_hooks, claude_hooks)
        self.assertIn("UserPromptSubmit", install_hooks["hooks"])
        self.assertIn("Stop", install_hooks["hooks"])

        install_commands = [
            hook["command"]
            for event in install_hooks["hooks"].values()
            for group in event
            for hook in group["hooks"]
        ]
        windows_commands = [
            hook.get("commandWindows", "")
            for event in install_hooks["hooks"].values()
            for group in event
            for hook in group["hooks"]
        ]
        self.assertTrue(all("CODEX_PLUGIN_ROOT" in command for command in install_commands))
        self.assertTrue(all("CLAUDE_PLUGIN_ROOT" in command for command in install_commands))
        self.assertTrue(all("chat2skill_hook.py" in command for command in install_commands))
        self.assertTrue(all("os.environ.get" in command for command in install_commands))
        self.assertTrue(all("sh -c" not in command for command in install_commands))
        self.assertTrue(all(command.startswith("cmd /d /s /c") for command in windows_commands))
        self.assertTrue(all("chat2skill_hook.cmd" in command for command in windows_commands))
        self.assertTrue((ROOT / "scripts" / "chat2skill_hook.cmd").exists())

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
        self.assertTrue(any("stop-response-guard" in command for command in commands))


if __name__ == "__main__":
    unittest.main()
