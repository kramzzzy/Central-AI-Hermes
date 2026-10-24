import base64
import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

import hermes_artwork as artwork


class SocialArtworkTests(unittest.TestCase):
    def response(self, uri=None):
        uri = uri or 'data:image/png;base64,' + base64.b64encode(b'x' * 600).decode()
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            'choices': [{'message': {'images': [{'image_url': {'url': uri}}]}}]
        }).encode()
        return response

    @patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test-only', 'SOCIAL_IMAGE_MODEL': ''})
    @patch('hermes_artwork.urllib.request.build_opener')
    def test_preserves_exact_prompt_and_uses_selected_model(self, opener):
        opener.return_value.open.return_value = self.response()
        prompt = 'Create a graphic with the headline "Explore solar" and our brand.'
        result = artwork.generate({'mode': 'social', 'prompt': prompt})
        payload = json.loads(opener.return_value.open.call_args.args[0].data)
        self.assertEqual(payload['messages'][1], {'role': 'user', 'content': [{'type': 'text', 'text': prompt}]})
        self.assertEqual(payload['model'], 'google/gemini-nano-banana-2.1')
        self.assertEqual(result['data'][0]['model'], payload['model'])
        self.assertEqual(payload['image_config']['aspect_ratio'], '3:4')

    @patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test-only'})
    @patch('hermes_artwork.urllib.request.build_opener')
    def test_references_reach_provider_and_urls_are_rejected(self, opener):
        opener.return_value.open.return_value = self.response()
        reference = 'data:image/png;base64,' + base64.b64encode(b'reference fixture').decode()
        artwork.generate({'mode': 'social', 'prompt': 'Use this product reference', 'references': [reference]})
        payload = json.loads(opener.return_value.open.call_args.args[0].data)
        self.assertEqual(payload['messages'][1]['content'][1], {'type': 'image_url', 'image_url': {'url': reference}})
        for references in [['https://example.com/image.png'], [reference] * 7, ['data:image/png;base64,'], 'invalid']:
            with self.assertRaises(artwork.ArtworkError):
                artwork.generate({'mode': 'social', 'prompt': 'Use this reference', 'references': references})
        self.assertEqual(opener.return_value.open.call_count, 1)

    @patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test-only', 'SOCIAL_IMAGE_MODEL': 'configured/image-model'})
    @patch('hermes_artwork.urllib.request.build_opener')
    def test_configured_model_and_no_image_failure(self, opener):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"choices":[{"message":{"content":"No image"}}]}'
        opener.return_value.open.return_value = response
        with self.assertRaisesRegex(artwork.ArtworkError, 'no image'):
            artwork.generate({'mode': 'social', 'prompt': 'Solar graphic'})
        self.assertEqual(json.loads(opener.return_value.open.call_args.args[0].data)['model'], 'configured/image-model')
        self.assertEqual(opener.return_value.open.call_count, 1)

    @patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test-only'})
    @patch('hermes_artwork.urllib.request.build_opener')
    def test_rejects_remote_urls_without_fetching(self, opener):
        opener.return_value.open.return_value = self.response('http://127.0.0.1/private')
        with self.assertRaises(artwork.ArtworkError):
            artwork.generate({'mode': 'social', 'prompt': 'Solar graphic'})
        self.assertEqual(opener.return_value.open.call_count, 1)

    @patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test-only'})
    @patch('hermes_artwork.urllib.request.build_opener')
    def test_provider_failure_does_not_fallback_or_leak_body(self, opener):
        opener.return_value.open.side_effect = urllib.error.HTTPError('https://openrouter.ai', 402, 'secret provider detail', {}, None)
        with self.assertRaisesRegex(artwork.ArtworkError, 'HTTP 402') as error:
            artwork.generate({'mode': 'social', 'prompt': 'Solar graphic'})
        self.assertNotIn('secret', str(error.exception))
        self.assertEqual(opener.return_value.open.call_count, 1)

    @patch.dict('os.environ', {'OPENROUTER_API_KEY': ''})
    def test_missing_key_and_invalid_prompt(self):
        with self.assertRaisesRegex(artwork.ArtworkError, 'OPENROUTER_API_KEY'):
            artwork.generate({'mode': 'social', 'prompt': 'Solar graphic'})
        for prompt in ['', 'ab', 'x' * 1401, None]:
            with self.assertRaises(artwork.ArtworkError):
                artwork.generate({'mode': 'social', 'prompt': prompt})


if __name__ == '__main__':
    unittest.main()
