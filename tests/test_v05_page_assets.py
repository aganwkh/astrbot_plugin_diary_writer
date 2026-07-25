import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "pages" / "diary-manager"


class PluginPageAssetTests(unittest.TestCase):
    def test_page_uses_relative_split_assets_and_bridge(self):
        index = (PAGE / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="./styles.css"', index)
        self.assertIn('src="./app.js"', index)
        for name in ("api.js", "state.js", "render.js", "charts.js", "app.js", "styles.css"):
            self.assertTrue((PAGE / name).is_file(), name)
        self.assertIn("window.AstrBotPluginPage", (PAGE / "api.js").read_text(encoding="utf-8"))
        self.assertIn('endpoint("overview")', (PAGE / "api.js").read_text(encoding="utf-8"))
        self.assertIn('endpoint("calendar")', (PAGE / "api.js").read_text(encoding="utf-8"))
        app = (PAGE / "app.js").read_text(encoding="utf-8")
        self.assertIn("Promise.all([diaryApi.overview(), diaryApi.calendar()])", app)
        self.assertIn("async function openEntry", app)

    def test_untrusted_content_is_never_inserted_as_html_or_persisted(self):
        sources = "\n".join(path.read_text(encoding="utf-8") for path in PAGE.glob("*.js"))
        self.assertNotIn("innerHTML", sources)
        self.assertNotIn("insertAdjacentHTML", sources)
        self.assertNotIn("localStorage", sources)
        self.assertNotIn("sessionStorage", sources)
        self.assertIn("textContent", sources)
        self.assertIn("createTextNode", sources)

    def test_theme_mobile_and_i18n_are_present_without_private_data(self):
        css = (PAGE / "styles.css").read_text(encoding="utf-8")
        self.assertIn('[data-theme="dark"]', css)
        self.assertIn("@media (max-width: 720px)", css)
        payload = json.loads((ROOT / ".astrbot-plugin" / "i18n" / "zh-CN.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["pages"]["diary-manager"]["title"], "日记管理")
        all_assets = "\n".join(path.read_text(encoding="utf-8") for path in PAGE.rglob("*") if path.is_file())
        self.assertNotIn("password", all_assets.casefold())
        self.assertNotIn("token=", all_assets.casefold())


if __name__ == "__main__":
    unittest.main()
