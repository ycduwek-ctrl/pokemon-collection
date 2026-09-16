import asyncio
import io
import unittest
from unittest.mock import MagicMock, patch
from PIL import Image
from fastapi import HTTPException
import main


class ReferenceDownloadTests(unittest.TestCase):
    def setUp(self):
        main._download_reference_image.cache_clear()

    def test_unknown_card_and_missing_access_never_fetch_network(self):
        with patch.object(main, 'require_access', side_effect=HTTPException(401)), patch.object(main.requests, 'get') as get:
            with self.assertRaises(HTTPException):
                asyncio.run(main.catalog_reference_image('2024sv-1', authorization=None))
            get.assert_not_called()
        with patch.object(main, 'require_access'), patch.object(main.requests, 'get') as get:
            with self.assertRaises(HTTPException) as error:
                asyncio.run(main.catalog_reference_image('https://localhost/secret', authorization='test'))
            self.assertEqual(error.exception.status_code, 404)
            get.assert_not_called()

    def test_valid_image_is_returned_without_forwarding_credentials(self):
        out = io.BytesIO()
        Image.new('RGB', (63, 88), 'blue').save(out, format='PNG')
        response = MagicMock(status_code=200)
        response.iter_content.return_value = [out.getvalue()]
        response.__enter__.return_value = response
        with patch.object(main, 'require_access'), patch.object(main.requests, 'get', return_value=response) as get:
            result = asyncio.run(main.catalog_reference_image('2024sv-1', authorization='private-token'))
            self.assertEqual(result.media_type, 'image/jpeg')
            with Image.open(io.BytesIO(result.body)) as image:
                self.assertEqual(image.size, (63, 88))
            self.assertNotIn('headers', get.call_args.kwargs)
            self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def test_untrusted_host_redirect_and_html_are_rejected(self):
        with patch.object(main.requests, 'get') as get:
            with self.assertRaises(ValueError):
                main._download_reference_image('https://127.0.0.1/admin')
            get.assert_not_called()
        for status, data in [(302, b''), (200, b'<html>Error</html>')]:
            response = MagicMock(status_code=status)
            response.iter_content.return_value = [data]
            response.__enter__.return_value = response
            with patch.object(main.requests, 'get', return_value=response):
                with self.assertRaises((OSError, ValueError)):
                    main._download_reference_image('https://pkmncards.com/wp-content/uploads/test.jpg')
