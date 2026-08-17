import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UiShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.auth = (ROOT / "hitim-auth.js").read_text(encoding="utf-8")

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

    def test_persistent_bottom_navigation_replaces_old_menus(self):
        self.assertIn('<nav class="bottom-nav"', self.html)
        for button_id in (
            "navGalleryBtn",
            "navSearchBtn",
            "navAddBtn",
            "navCameraBtn",
            "navAdminBtn",
            "navShareBtn",
            "navSettingsBtn",
        ):
            self.assertIn(f'id="{button_id}"', self.html)
        self.assertNotIn('class="header-menu"', self.html)
        self.assertNotIn('class="floating-actions"', self.html)
        self.assertNotIn('class="quick-identify-bar"', self.html)

    def test_camera_navigation_is_icon_only(self):
        camera_button = self.html.split('id="navCameraBtn"', 1)[1].split(
            "</button>", 1
        )[0]
        self.assertIn("<svg", camera_button)
        self.assertNotIn("<span>זיהוי מהיר", camera_button)
        self.assertIn('class="visually-hidden"', camera_button)

    def test_app_icon_is_used_in_header(self):
        header = self.html.split('<div class="header">', 1)[1].split(
            '<div class="active-filters"', 1
        )[0]
        self.assertIn('src="/hitim-icon-193.png"', header)

    def test_gallery_cards_show_only_image_and_price(self):
        image_markup = self.html.split("function cardImageMarkup", 1)[1].split(
            "function bindGalleryCarousel", 1
        )[0]
        self.assertIn("card-price-badge", image_markup)
        self.assertNotIn("rarity-badge", image_markup)
        self.assertNotIn("comments-badge", image_markup)
        card_node = self.html.split("function getCardNode", 1)[1].split(
            "function render()", 1
        )[0]
        self.assertNotIn("card-body", card_node)
        self.assertNotIn("card-name", card_node)
        render = self.html.split("function render()", 1)[1].split(
            "function openDetail", 1
        )[0]
        self.assertNotIn("add-card", render)

    def test_catalog_search_and_simple_price_sort(self):
        self.assertIn("מציאה בקטלוג המלא", self.html)
        self.assertIn('id="catalogSearchName"', self.html)
        self.assertIn('id="catalogSearchNumber"', self.html)
        self.assertIn("function searchCatalogCards", self.html)
        self.assertIn("function chooseCatalogSearchResult", self.html)
        self.assertIn("מהיקר לזול", self.html)
        self.assertIn("מהזול ליקר", self.html)
        for removed_filter in ("rarityChips", "condChips", "langChips", "valueMin", "yearMin"):
            self.assertNotIn(removed_filter, self.html)

    def test_removed_column_control_cannot_stop_app_startup(self):
        init_cols = self.html.split("function initCols()", 1)[1].split(
            "function applyGrid", 1
        )[0]
        self.assertIn("if(counter)counter.textContent=cols", init_cols)
        self.assertNotIn("document.getElementById('colsNum').textContent", init_cols)

    def test_sign_out_clears_local_session_even_if_startup_failed(self):
        sign_out = self.auth.split("async function signOut()", 1)[1].split(
            "async function refreshAccess", 1
        )[0]
        self.assertIn("finally", sign_out)
        self.assertIn("endsWith('-auth-token')", sign_out)
        self.assertIn("location.reload()", sign_out)

    def test_settings_show_install_and_exit_without_legacy_or_invite(self):
        self.assertNotIn('<div class="toolbar">', self.html)
        settings = self.html.split('id="settingsOverlay"', 1)[1].split(
            'id="adminOverlay"', 1
        )[0]
        self.assertIn("התקן את Hitim", settings)
        self.assertIn("הורד גיבוי", settings)
        self.assertIn("יציאה מהאפליקציה", settings)
        self.assertNotIn("שתף קישור כניסה", settings)
        self.assertNotIn("האוסף הישן", settings)
        self.assertNotIn("מחובר כ־", settings)

    def test_header_brand_uses_white_text_and_camera_palette(self):
        title_css = self.html.split(".header-title-text {", 1)[1].split("}", 1)[0]
        stats_css = self.html.split(".header-stats-box {", 1)[1].split("}", 1)[0]
        self.assertIn("color: #fff", title_css)
        self.assertIn("filter: none", title_css)
        self.assertIn("linear-gradient(150deg,#7c3aed 8%,#5b37e8 52%,#fbbf24 100%)", stats_css)

    def test_auth_spinner_does_not_repeat_the_brand_icon(self):
        spinner_css = self.html.split(".auth-spinner {", 1)[1].split("}", 1)[0]
        self.assertNotIn("hitim-icon", spinner_css)
        self.assertIn("border-top-color", spinner_css)

    def test_quick_identification_buttons_do_not_repeat_the_brand_icon(self):
        self.assertNotIn("inline-brand-icon", self.html)
        self.assertNotIn("⚡ זיהוי מהיר", self.html)


if __name__ == "__main__":
    unittest.main()
