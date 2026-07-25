import unittest
from pathlib import Path


class PluginEntrypointShapeTests(unittest.TestCase):
    def test_entrypoint_uses_configured_data_and_private_permission_gate(self):
        source = Path("main.py").read_text(encoding="utf-8")
        self.assertIn("get_astrbot_data_path", source)
        self.assertIn("from .diary.config import DiaryConfig", source)
        self.assertIn("can_access_sensitive_diary", source)
        self.assertIn("self.config.can_auto_write", source)
        self.assertIn('@filter.command("补写年记")', source)
        self.assertIn('@filter.command("重写年记")', source)
        self.assertNotIn("/opt" + "/AstrBot", source)
        self.assertNotIn("USER_ID", source)

    def test_readme_lists_every_registered_command(self):
        import re

        commands = re.findall(r'@filter\.command\("([^"]+)', Path("main.py").read_text(encoding="utf-8"))
        readme = Path("README.md").read_text(encoding="utf-8")
        for command in commands:
            self.assertIn(f"`/{command}", readme, command)

    def test_release_version_is_consistent(self):
        version = "1.1.0"
        self.assertIn(f"version: {version}", Path("metadata.yaml").read_text(encoding="utf-8"))
        self.assertIn(f'"{version}"', Path("main.py").read_text(encoding="utf-8"))
        self.assertIn(f'"plugin_version": "{version}"', Path("diary/archives.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
