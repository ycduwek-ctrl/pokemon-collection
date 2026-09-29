import unittest
import card_catalog as catalog

class AsianCoverageTests(unittest.TestCase):
    def test_photographed_sets_have_complete_reference_packs(self):
        for language, code, expected in [('Japanese','M2a',250),('Japanese','SMH',131),('Chinese (Simplified)','CBB1C',115)]:
            with self.subTest(code=code):
                pack=catalog.download_set_cards(language,code)
                self.assertEqual(len(pack['cards']),expected)
                self.assertEqual(pack['imageCount'],expected)
                self.assertEqual(len({c['catalogCardId'] for c in pack['cards']}),expected)

    def test_exact_photographed_printings(self):
        for language,code,number,cid in [
            ('Japanese','M2a','208/193','M2a-208'),
            ('Japanese','M2a','225/193','M2a-225'),
            ('Japanese','M2a','063/193','M2a-063'),
            ('Japanese','SMH','013/131','SMH-013'),
            ('Chinese (Simplified)','CBB1C','07 03/09','CBB1C-0703')]:
            with self.subTest(cid=cid):
                result=catalog.lookup_card(dict(language=language,setCode=code,number=number))
                self.assertEqual(result['catalogCardId'],cid)
                self.assertTrue(result['catalogImage'])
                found=catalog.search_catalog(number=number,language=language,set_code=code)
                self.assertEqual(found['candidates'][0]['catalogCardId'],cid)

    def test_gem_variants_are_distinct_and_wrong_denominator_rejected(self):
        a=catalog.search_catalog(number='0703/09',language='Chinese (Simplified)',set_code='CBB1C')
        b=catalog.search_catalog(number='07 04/09',language='Chinese (Simplified)',set_code='CBB1C')
        self.assertNotEqual(a['candidates'][0]['catalogImage'],b['candidates'][0]['catalogImage'])
        self.assertEqual(a['candidates'][0]['number'],'07 03/09')
        self.assertEqual(catalog.search_catalog(number='07 03/08',language='Chinese (Simplified)',set_code='CBB1C')['candidateTotal'],0)

    def test_gem_ocr_keeps_composite_number(self):
        results=catalog._rank_ocr_candidates('CBB1C 07 03/09 船长皮卡丘')
        self.assertTrue(results)
        self.assertEqual(results[0]['payload']['catalogCardId'],'CBB1C-0703')
