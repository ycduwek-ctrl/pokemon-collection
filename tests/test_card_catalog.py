import tempfile
import unittest
from pathlib import Path

import card_catalog


class CardCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="hitim-test-catalog-")
        card_catalog.DATABASE_PATH = Path(cls.temporary.name) / "catalog.sqlite3"
        card_catalog._catalog_ready = False
        assert card_catalog.ensure_catalog()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_catalog_has_multilingual_coverage(self):
        status = card_catalog.catalog_status()
        self.assertTrue(status["ready"])
        self.assertGreater(status["cards"], 100_000)
        self.assertGreaterEqual(status["languages"], 10)

    def test_exact_english_printing(self):
        match = card_catalog.lookup_card({
            "language": "English",
            "number": "4/102",
            "set": "Base Set",
            "setCode": "base1",
            "printedName": "Charizard",
            "name": "Charizard (צ'ריזארד)",
        })
        self.assertEqual(match["catalogCardId"], "base1-4")
        self.assertEqual(match["number"], "4/102")
        self.assertEqual(match["catalogEnglishName"], "Charizard")

    def test_same_printing_in_french(self):
        match = card_catalog.lookup_card({
            "language": "French",
            "number": "4/102",
            "set": "Set de Base",
            "setCode": "base1",
            "printedName": "Dracaufeu",
            "name": "Charizard (צ'ריזארד)",
        })
        self.assertEqual(match["catalogCardId"], "base1-4")
        self.assertEqual(match["printedName"], "Dracaufeu")
        self.assertIn("/fr/", match["catalogImage"])

    def test_japanese_collector_number(self):
        match = card_catalog.lookup_card({
            "language": "Japanese",
            "number": "025",
            "setCode": "sv2a",
            "printedName": "ピカチュウ",
            "name": "Pikachu (פיקאצ'ו)",
        })
        self.assertEqual(match["catalogCardId"].casefold(), "sv2a-025".casefold())
        self.assertEqual(match["number"], "025/165")

    def test_ambiguous_number_is_not_guessed(self):
        self.assertIsNone(card_catalog.lookup_card({
            "language": "English",
            "number": "4",
            "name": "Unknown",
        }))


if __name__ == "__main__":
    unittest.main()
