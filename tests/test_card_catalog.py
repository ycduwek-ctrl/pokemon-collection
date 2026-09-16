import tempfile
import unittest
from pathlib import Path

import card_catalog


class CardCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_path = card_catalog.DATABASE_PATH
        cls.temporary = tempfile.TemporaryDirectory(prefix="hitim-test-catalog-")
        card_catalog.DATABASE_PATH = Path(cls.temporary.name) / "catalog.sqlite3"
        card_catalog._catalog_ready = False
        assert card_catalog.ensure_catalog()

    @classmethod
    def tearDownClass(cls):
        card_catalog.DATABASE_PATH = cls.original_path
        card_catalog._catalog_ready = False
        cls.temporary.cleanup()

    def test_traditional_chinese_supplement(self):
        match = card_catalog.lookup_card({"language": "Chinese", "number": "035/184", "setCode": "AS5a", "name": "Blastoise"})
        self.assertEqual(match["catalogCardId"], "AS5a-035")
        result = card_catalog.lookup_ocr_result("AS5a C 035/184 水箭龜 HP 160")
        self.assertEqual(result["match"]["catalogCardId"], "AS5a-035")

    def test_assisted_chinese_search_preserves_printing(self):
        result = card_catalog.search_catalog(name="Blastoise", number="035/184", language="Chinese", set_code="AS5a")
        self.assertEqual([c["catalogCardId"] for c in result["candidates"]], ["AS5a-035"])
        for language, code in [("Chinese (Simplified)", "AS5a"), ("Chinese", "AS99a")]:
            self.assertEqual(card_catalog.search_catalog(number="035/184", language=language, set_code=code)["candidates"], [])

    def test_missing_chinese_set_does_not_return_unrelated_cards(self):
        self.assertIsNone(card_catalog.lookup_card({"language": "Chinese", "number": "035/184", "setCode": "AS99a", "name": "Blastoise"}))
        result = card_catalog.lookup_ocr_result("AS99a C 035/184 水箭龜 HP 160")
        self.assertFalse(result.get("candidates"))
        self.assertFalse(result.get("match"))

    def test_downloadable_ascended_heroes_is_complete_and_sorted(self):
        sets = card_catalog.list_download_sets("English")
        featured = sets[0]
        self.assertEqual(featured["id"], "me02.5")
        self.assertEqual(featured["available"], 295)
        result = card_catalog.download_set_cards("English", "me02.5")
        self.assertEqual(len(result["cards"]), 295)
        self.assertEqual(result["cards"][0]["number"], "001/217")
        self.assertEqual(result["cards"][-1]["number"], "295/217")
        self.assertTrue(all(c["language"] == "English" and c["setCode"] == "me02.5" for c in result["cards"]))
        self.assertEqual(len({c["catalogCardId"] for c in result["cards"]}), 295)

    def test_downloadable_sets_do_not_cross_languages_or_accept_sql(self):
        self.assertEqual(card_catalog.download_set_cards("Japanese", "me02.5")["cards"], [])
        self.assertEqual(card_catalog.download_set_cards("English", "' OR 1=1 --")["cards"], [])
        self.assertEqual(card_catalog.list_download_sets("unsupported"), [])

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

    def test_manual_name_search_keeps_the_full_variant_name(self):
        result = card_catalog.search_catalog("Pikachu V", limit=12)
        self.assertTrue(result["candidates"])
        self.assertTrue(all(
            candidate["name"] == "Pikachu V"
            for candidate in result["candidates"]
        ))
        self.assertTrue(all(candidate["catalogImage"] for candidate in result["candidates"]))

    def test_manual_search_accepts_name_and_number_together(self):
        result = card_catalog.search_catalog("Pikachu V", "043/185")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["catalogCardId"], "swsh4-43")
        self.assertEqual(result["candidates"][0]["name"], "Pikachu V")

    def test_manual_search_accepts_number_without_name(self):
        result = card_catalog.search_catalog(number="043/185")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["catalogCardId"], "swsh4-43")


class EnrichedSetTests(unittest.TestCase):
    def test_all_mcdonalds_english_editions_have_printing_images(self):
        sets = [s for s in card_catalog.list_download_sets('English') if "McDonald" in s['name']]
        self.assertEqual(len(sets), 12)
        for item in sets:
            self.assertEqual(item['imageCount'], item['available'], item['name'])
            cards = card_catalog.download_set_cards('English', item['id'])['cards']
            self.assertTrue(all(c['catalogImage'] and c['setCode'] == item['id'] for c in cards))

    def test_printed_mcdonalds_codes_override_reused_artwork(self):
        for text, identifier in [('Charizard M24EN 001/015', '2024sv-1'),
                                 ('Sprigatito M23 EN 001/015', '2023sv-1'),
                                 ('Pikachu MCD21 25/25', '2021swsh-25')]:
            result = card_catalog.lookup_ocr_result(text)
            self.assertEqual(result['match']['catalogCardId'], identifier)
        result = card_catalog.search_catalog('Charizard', '001/015', language='English', set_code='M24EN')
        self.assertEqual([c['catalogCardId'] for c in result['candidates']], ['2024sv-1'])
        self.assertFalse(card_catalog.search_catalog('Charizard', '001/015', language='English', set_code='M23')['candidates'])

    def test_hit_rank_uses_rarity_and_image_urls_keep_their_provider_format(self):
        cards = card_catalog.download_set_cards('English', 'me02.5')['cards']
        highest = sorted(cards, key=lambda c: c['hitRank'], reverse=True)[0]
        self.assertEqual(highest['rarity'], 'Mega Hyper Rare')
        self.assertGreater(highest['hitRank'], cards[0]['hitRank'])
        self.assertTrue(all(not u.endswith('/large/high.webp') for c in cards for u in c['imageSources']))
        self.assertEqual(card_catalog._image_url('https://images.scrydex.com/pokemon/x/large'), 'https://images.scrydex.com/pokemon/x/large')
        self.assertEqual(card_catalog.reference_image_sources('Japanese', '2024sv-1'), [])
        self.assertEqual(card_catalog.reference_image_sources('English', 'https://localhost/secret'), [])


if __name__ == "__main__":
    unittest.main()
