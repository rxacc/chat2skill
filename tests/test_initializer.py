import tempfile
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from chat2skill import initializer, storage
from chat2skill import config as chat2skill_config


class InitializerTests(unittest.TestCase):
    def test_ensure_user_home_creates_config_db_and_skill_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_home = Path(tmp) / ".chat2skill"
            db_path = data_home / "c2s.db"
            skill_dir = data_home / "skills"
            with patch.object(chat2skill_config, "DATA_HOME", data_home):
                with patch.object(chat2skill_config, "CONFIG_PATH", data_home / "config.json"):
                    with patch.object(chat2skill_config, "CONTEXTS_DIR", data_home / "contexts"):
                        with patch.object(storage, "DB_PATH", db_path):
                            with patch.object(storage, "LEGACY_DB_PATH", data_home / "chat2skill.db"):
                                with patch.object(storage, "SKILL_DIR", skill_dir):
                                    result = initializer.ensure_user_home(create_db=True)

            self.assertEqual(result["data_home"], str(data_home))
            self.assertTrue((data_home / "config.json").exists())
            self.assertTrue(db_path.exists())
            self.assertTrue(skill_dir.exists())
            self.assertTrue((data_home / "contexts").exists())
            self.assertTrue(result["created_config"])

    def test_hook_launcher_initializes_user_home_before_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_home = Path(tmp) / ".chat2skill"
            env = os.environ.copy()
            env["CHAT2SKILL_HOME"] = str(data_home)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "scripts" / "chat2skill_hook.py"),
                    "stop-response-guard",
                ],
                input="{}\n",
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((data_home / "config.json").exists())
            self.assertTrue((data_home / "c2s.db").exists())
            self.assertTrue((data_home / "skills").exists())


if __name__ == "__main__":
    unittest.main()
