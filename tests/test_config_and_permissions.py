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

    def test_chihaya_preset_keeps_voice_but_identity_is_configured(self):
        config = load_module("diary.config")
        settings = config.DiaryConfig.from_mapping({"owner_ids": ["1"], "persona_preset": "chihaya_anon", "user_nickname": "虾仁"})
        persona = settings.persona
        self.assertEqual(persona.name, "千早爱音")
        self.assertEqual(settings.user_nickname, "虾仁")
        self.assertIn("第一人称", persona.voice)

    def test_disabled_auto_write_is_effective(self):
        config = load_module("diary.config")
        settings = config.DiaryConfig.from_mapping({"owner_ids": ["1"], "auto_write_enabled": False})
        self.assertFalse(settings.auto_write_enabled)


if __name__ == "__main__":
    unittest.main()
