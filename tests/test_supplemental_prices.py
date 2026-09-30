import unittest
from unittest.mock import patch, Mock
import requests
import main
from supplemental_prices import PRINTINGS, parse_price, supplemental_price

P = PRINTINGS[('zh-cn', 'CBB1C-0703')]

def page(price='$4.18'):
    return ('<link rel="canonical" href="https://www.pricecharting.com/game/'+P['path']+'">'
            '<h1 id="product_name" title="8710183">Captain Pikachu [Stars] #703</h1>'
            '<a id="dropdown_selected_currency">USD</a>'
            '<td id="used_price"><span class="price js-price">'+price+'</span>'
            '<span class="change">$0.70</span></td>'
            '<td id="new_price"><span class="price js-price">$500.00</span></td>')

class SupplementalPricesTests(unittest.TestCase):
    def setUp(self):
        main._price_result_cache.clear()

    def test_only_ungraded_value(self):
        self.assertEqual(parse_price(page(),P),'4.18')
        self.assertIsNone(parse_price(page('-'),P))
        self.assertIsNone(parse_price(page('$0.00'),P))

    def test_wrong_identity_or_currency_fails_closed(self):
        for text in [page().replace('8710183','8710187'),page().replace('[Stars]','[Stamped]'),page().replace('USD','CAD'),page().replace(P['path'],'wrong')]:
            self.assertIsNone(parse_price(text,P))

    def test_supplement_reaches_price_source_and_caches(self):
        info={'language':'Chinese (Simplified)','catalogCardId':'CBB1C-0703','number':'07 03/09'}
        with patch('supplemental_prices.requests.get',return_value=Mock(status_code=200,text=page())) as get:
            result=main._market_price_for_card(info)
            self.assertEqual(result['value'],'4.18')
            self.assertEqual(result['priceSource'],'PriceCharting')
            self.assertTrue(result['catalogImage'])
            main._market_price_for_card(info)
            self.assertEqual(get.call_count,1)
            self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def test_unmapped_variant_does_not_borrow_price(self):
        with patch('supplemental_prices.requests.get') as get:
            result=main._market_price_for_card({'language':'Chinese (Simplified)','catalogCardId':'CBB1C-0704','number':'07 04/09'})
            self.assertEqual(result['value'],'')
            get.assert_not_called()

    def test_network_failure_and_conflicting_number(self):
        supplement={'language':'zh-cn','cardId':'CBB1C-0703'}
        with patch('supplemental_prices.requests.get',side_effect=requests.Timeout) as get:
            self.assertEqual(supplemental_price(supplement,{'number':'07 03/09'})['priceStatus'],'service-unavailable')
            get.reset_mock()
            self.assertEqual(supplemental_price(supplement,{'number':'07 04/09'})['priceStatus'],'variant-ambiguous')
            get.assert_not_called()
