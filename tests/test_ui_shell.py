import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UiShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "index.html").read_text(encoding="utf-8")

    def test_holo_and_motion_code_are_completely_removed(self):
        self.assertNotIn("card-holo-effect", self.html)
        self.assertNotIn("deviceorientation", self.html)
        self.assertNotIn("DeviceOrientationEvent", self.html)
        self.assertNotIn("initHoloEffect", self.html)

    def test_gallery_image_tap_opens_card_but_swipe_does_not(self):
        self.assertIn("function bindGalleryCarousel", self.html)
        self.assertIn("if(shouldOpen)openDetail(cardId)", self.html)
        self.assertIn("didSwipe=true", self.html)
        gallery_markup = self.html.split(
            "function cardImageMarkup", 1
        )[1].split("function bindGalleryCarousel", 1)[0]
        self.assertNotIn('onclick="event.stopPropagation()"', gallery_markup)

    def test_carousel_is_optimized_for_stable_horizontal_swiping(self):
        carousel_css = self.html.split(".card-carousel-track {", 1)[1].split(
            "}", 1
        )[0]
        self.assertIn("direction: ltr", carousel_css)
        self.assertIn("touch-action: pan-x pan-y", carousel_css)
        self.assertIn("overscroll-behavior-x: contain", carousel_css)
        self.assertIn("scroll-snap-stop: always", self.html)

    def test_expensive_mobile_painting_features_are_not_used(self):
        self.assertNotIn("backdrop-filter", self.html)
        self.assertNotIn("content-visibility", self.html)
        self.assertNotIn(".poke-card:active", self.html)

    def test_auth_spinner_does_not_repeat_the_brand_icon(self):
        spinner_css = self.html.split(".auth-spinner {", 1)[1].split("}", 1)[0]
        self.assertNotIn("hitim-icon", spinner_css)
        self.assertIn("border-top-color", spinner_css)

    def test_quick_identification_buttons_do_not_repeat_the_brand_icon(self):
        self.assertNotIn("inline-brand-icon", self.html)
        self.assertNotIn("⚡ זיהוי מהיר", self.html)


if __name__ == "__main__":
    unittest.main()
