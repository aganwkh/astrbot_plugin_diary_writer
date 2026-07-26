import importlib
import unittest


def load_module(name):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        raise AssertionError(f"missing module: {name}") from exc


class Event:
    def __init__(self, sender_id, origin):
        self.sender_id = sender_id
        self.unified_msg_origin = origin

    def get_sender_id(self):
        return self.sender_id


class ConfigAndPermissionTests(unittest.TestCase):
    def test_empty_owner_list_disables_access(self):
        config = load_module("diary.config")
        permissions = load_module("diary.permissions")
        settings = config.DiaryConfig.from_mapping({"owner_ids": []})
        self.assertFalse(permissions.is_authorized(Event("1", "qq:FriendMessage:1"), settings))

    def test_sensitive_access_requires_authorized_private_event(self):
        config = load_module("diary.config")
        permissions = load_module("diary.permissions")
        settings = config.DiaryConfig.from_mapping({"owner_ids": ["1"]})
        self.assertTrue(permissions.can_access_sensitive_diary(Event("1", "qq:FriendMessage:1"), settings))
        self.assertFalse(permissions.can_access_sensitive_diary(Event("1", "qq:GroupMessage:2"), settings))
        group_enabled = config.DiaryConfig.from_mapping({"owner_ids": ["1"], "allow_group_commands": ["补写年记"]})
        self.assertFalse(permissions.can_use_group_command(Event("1", "qq:GroupMessage:2"), group_enabled, "补写年记"))

    def test_legacy_plugin_persona_settings_are_ignored(self):
        config = load_module("diary.config")
        settings = config.DiaryConfig.from_mapping({"owner_ids": ["1"], "persona_preset": "chihaya_anon", "user_nickname": "虾仁"})
        self.assertFalse(hasattr(settings, "persona"))
        self.assertFalse(hasattr(settings, "persona_preset"))
        self.assertFalse(hasattr(settings, "user_nickname"))

    def test_diary_main_prompt_is_user_configurable(self):
        config = load_module("diary.config")
        settings = config.DiaryConfig.from_mapping({"diary_main_prompt": "我的自定义主提示词"})
        self.assertEqual(settings.diary_main_prompt, "我的自定义主提示词")
        self.assertEqual(config.DiaryConfig().diary_main_prompt, "")

    def test_disabled_auto_write_is_effective(self):
        config = load_module("diary.config")
        settings = config.DiaryConfig.from_mapping({"owner_ids": ["1"], "auto_write_enabled": False})
        self.assertFalse(settings.auto_write_enabled)


if __name__ == "__main__":
    unittest.main()
