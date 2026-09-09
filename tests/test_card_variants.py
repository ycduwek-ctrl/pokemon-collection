import unittest
from unittest.mock import patch

try:
    import main
except ModuleNotFoundError:
    main = None


@unittest.skipIf(main is None, "backend dependencies are not installed")
class CardVariantTests(unittest.TestCase):
    def setUp(self):
        main._tcgplayer_variant_cache.clear()
        main._price_result_cache.clear()

    @staticmethod
    def sets():
        return [
            {"setNameId": 1, "name": "Vivid Voltage", "abbreviation": "SWSH04"},
            {"setNameId": 2, "name": "Battle Academy 2022", "abbreviation": "BA22"},
            {"setNameId": 3, "name": "Prize Pack Series One", "abbreviation": "PRZ1"},
            {"setNameId": 4, "name": "Jumbo Cards", "abbreviation": "JUMBO"},
        ]

    @staticmethod
    def guides(set_id, _product_type):
        rows = {
            1: [("226430", "Pikachu V", "Normal", 2.41)],
            2: [("268213", "Pikachu V (#60 Pikachu Stamped)", "Holofoil", 5.14)],
            3: [("490543", "Pikachu V", "Holofoil", 218.68)],
            4: [("491634", "Pikachu V (Vivid Voltage Stamped)", "Holofoil", 20.00)],
        }
        return [
            {
                "productID": product_id,
                "productName": name,
                "number": "043/185",
                "printing": printing,
                "condition": "Near Mint",
                "marketPrice": price,
            }
            for product_id, name, printing, price in rows.get(set_id, [])
        ]

    @patch.object(main, "_tcgplayer_price_guide", side_effect=guides.__func__)
    @patch.object(main, "_tcgplayer_sets", return_value=sets.__func__())
    def test_returns_regular_stamped_prize_pack_and_jumbo(self, _sets, _guide):
        result = main._tcgplayer_variant_options({
            "name": "Pikachu V", "number": "043/185", "set": "Vivid Voltage",
            "setCode": "swsh4", "language": "English",
        })
        by_id = {option["tcgplayerProductId"]: option for option in result["options"]}
        self.assertEqual(result["status"], "matched")
        self.assertEqual(by_id["226430"]["variantKind"], "normal")
        self.assertEqual(by_id["268213"]["variantKind"], "stamped")
        self.assertEqual(by_id["490543"]["variantKind"], "prize-pack")
        self.assertEqual(by_id["491634"]["variantKind"], "jumbo")

    @patch.object(main, "_localized_tcgdex_price")
    @patch.object(main, "_tcgplayer_price_guide", side_effect=guides.__func__)
    @patch.object(main, "_tcgplayer_sets", return_value=sets.__func__())
    def test_selected_stamped_printing_wins_over_generic_catalog_price(self, _sets, _guide, tcgdex):
        result = main._market_price_for_card({
            "name": "Pikachu V", "number": "043/185", "set": "Vivid Voltage",
            "setCode": "swsh4", "catalogCardId": "swsh4-43", "language": "English",
            "tcgplayerProductId": "268213", "marketPrinting": "Holofoil",
            "variantSet": "Battle Academy 2022", "variantKind": "stamped",
            "variantLabel": "Pikachu V (#60 Pikachu Stamped)",
        })
        self.assertEqual(result["priceStatus"], "matched")
        self.assertEqual(result["value"], "5.14")
        self.assertEqual(result["tcgplayerProductId"], "268213")
        self.assertEqual(result["variantKind"], "stamped")
        tcgdex.assert_not_called()


if __name__ == "__main__":
    unittest.main()
