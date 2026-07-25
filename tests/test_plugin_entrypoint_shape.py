import unittest
from pathlib import Path


class PluginEntrypointShapeTests(unittest.TestCase):
    def test_entrypoint_uses_configured_data_and_private_permission_gate(self):
        source = Path("main.py").read_text(encoding="utf-8")
        self.assertIn("get_astrbot_data_path", source)
        self.assertIn("can_access_sensitive_diary", source)
        self.assertIn("self.config.can_auto_write", source)
        self.assertIn('@filter.command("补写年记")', source)
        self.assertIn('@filter.command("重写年记")', source)
        self.assertNotIn("/opt" + "/AstrBot", source)
        self.assertNotIn("USER_ID", source)


if __name__ == "__main__":
    unittest.main()
