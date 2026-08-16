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

    def test_ocr_uses_name_to_resolve_shared_numbering(self):
        match = card_catalog.lookup_ocr_text(
            "Charizard 120 HP Energy Burn Fire Spin 4 / 102"
        )
        self.assertIsNotNone(match)
        self.assertEqual(match["catalogCardId"], "base1-4")
        self.assertEqual(match["name"], "Charizard")

    def test_ocr_repairs_common_digit_confusions_and_reads_set_code(self):
        match = card_catalog.lookup_ocr_text(
            "SV2a Pikachu Thunder Jolt O25 / 165"
        )
        self.assertIsNotNone(match)
        self.assertEqual(match["catalogCardId"].casefold(), "sv2a-025")

    def test_ocr_does_not_guess_an_ambiguous_number(self):
        self.assertIsNone(card_catalog.lookup_ocr_text("025 / 102"))

    def test_ocr_repairs_slash_read_as_seven(self):
        match = card_catalog.lookup_ocr_text(
            "Teal Mask Ogerpon ex Pokemon eX rule 0257167 2024"
        )
        self.assertIsNotNone(match)
        self.assertEqual(match["catalogCardId"], "sv06-025")

    def test_ocr_repairs_extra_leading_digit(self):
        match = card_catalog.lookup_ocr_text(
            "Victini V V rule 925/202 2020"
        )
        self.assertIsNotNone(match)
        self.assertEqual(match["catalogCardId"], "swsh1-25")

    def test_ocr_repairs_missing_denominator_digit(self):
        match = card_catalog.lookup_ocr_text(
            "Charizard Energy Burn 4/02 1999"
        )
        self.assertIsNotNone(match)
        self.assertEqual(match["catalogCardId"], "base1-4")

    def test_noisy_number_returns_correct_visual_candidate(self):
        result = card_catalog.lookup_ocr_result(
            "BASIC Charcade Protect G2005/782 2023"
        )
        self.assertIsNone(result["match"])
        self.assertGreaterEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["catalogCardId"], "sv04-025")

    def test_name_only_returns_ranked_candidates_instead_of_failure(self):
        result = card_catalog.lookup_ocr_result(
            "BASIC Torkoal 130 HP Live Coal 2025"
        )
        self.assertIsNone(result["match"])
        self.assertGreaterEqual(len(result["candidates"]), 2)
        self.assertEqual(result["candidates"][0]["catalogCardId"], "sv09-025")

    def test_artwork_fragment_does_not_override_card_name(self):
        result = card_catalog.lookup_ocr_result(
            "BASIC Torkoal 130 HP EE0/2 Live Coal 2025"
        )
        self.assertIsNone(result["match"])
        self.assertEqual(result["candidates"][0]["catalogCardId"], "sv09-025")

    def test_candidate_pages_return_more_exact_printings(self):
        first = card_catalog.lookup_ocr_result(
            "BASIC Torkoal 130 HP Live Coal 2025", limit=4, offset=0
        )
        second = card_catalog.lookup_ocr_result(
            "BASIC Torkoal 130 HP Live Coal 2025", limit=4, offset=4
        )
        first_ids = {candidate["catalogCardId"] for candidate in first["candidates"]}
        second_ids = {candidate["catalogCardId"] for candidate in second["candidates"]}
        self.assertTrue(first["hasMoreCandidates"])
        self.assertGreater(first["candidateTotal"], len(first["candidates"]))
        self.assertEqual(first_ids & second_ids, set())

    def test_visual_candidates_never_include_text_only_records(self):
        result = card_catalog.lookup_ocr_result("025 / 102", limit=12)
        self.assertIsNone(result["match"])
        self.assertTrue(result["candidates"])
        self.assertTrue(all(candidate["catalogImage"] for candidate in result["candidates"]))

    def test_foreign_exact_match_requests_an_english_display_name(self):
        result = card_catalog.lookup_ocr_result(
            "SV2a Pikachu Thunder Jolt O25 / 165"
        )
        self.assertIsNotNone(result["match"])
        self.assertEqual(result["match"]["language"], "Japanese")
        self.assertTrue(result["match"]["needsEnglishName"])


if __name__ == "__main__":
    unittest.main()
