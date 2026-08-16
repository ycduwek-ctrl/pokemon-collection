import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UiShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "index.html").read_text(encoding="utf-8")

    def test_gallery_cards_do_not_render_holo_layers(self):
        gallery_markup = self.html.split(
            "function cardImageMarkup", 1
        )[1].split("function getCardNode", 1)[0]
        self.assertNotIn("card-holo-effect", gallery_markup)

    def test_holo_effect_is_limited_to_detail_view_and_ignores_touches(self):
        self.assertIn(
            "event.target.closest('.detail-carousel')", self.html
        )
        self.assertIn("pointer-events: none", self.html)
        detail_markup = self.html.split("function openDetail", 1)[1].split(
            "function closeDetail", 1
        )[0]
        self.assertIn("card-holo-effect", detail_markup)

    def test_auth_spinner_does_not_repeat_the_brand_icon(self):
        spinner_css = self.html.split(".auth-spinner {", 1)[1].split("}", 1)[0]
        self.assertNotIn("hitim-icon", spinner_css)
        self.assertIn("border-top-color", spinner_css)

    def test_quick_identification_buttons_do_not_repeat_the_brand_icon(self):
        self.assertNotIn("inline-brand-icon", self.html)
        self.assertNotIn("⚡ זיהוי מהיר", self.html)


if __name__ == "__main__":
    unittest.main()
