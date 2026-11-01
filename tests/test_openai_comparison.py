import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from scripts.compare_openai import OpenAIComparison
from qq.core import QQError


class OpenAIComparisonTests(unittest.TestCase):
    def call(self, envelope):
        client = OpenAIComparison("test-key")
        with patch("scripts.compare_openai.urlopen",
                   return_value=io.StringIO(json.dumps(envelope))) as request:
            result = client("test-model", "ignored", {"type": "object"}, "Review code", {})
        return result, client, request

    def test_reads_message_after_reasoning_and_records_usage(self):
        result, client, request = self.call({
            "id": "response-1", "model": "test-model", "status": "completed",
            "usage": {"input_tokens": 100, "output_tokens": 20},
            "output": [{"type": "reasoning", "summary": []},
                       {"type": "message", "content": [{"type": "output_text", "text": '{"points": []}'}]}],
        })
        self.assertEqual(result, {"points": []})
        self.assertEqual(client.calls[0]["usage"]["input_tokens"], 100)
        sent = request.call_args.args[0]
        self.assertEqual(sent.full_url, "https://api.openai.com/v1/responses")
        body = json.loads(sent.data)
        self.assertFalse(body["store"])
        self.assertTrue(body["text"]["format"]["strict"])

    def test_incomplete_refused_and_invalid_outputs_are_errors(self):
        envelopes = [
            {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
            {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]},
            {"status": "completed", "output": []},
            {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "invalid"}]}]},
        ]
        for envelope in envelopes:
            with self.subTest(envelope=envelope), self.assertRaises(QQError):
                self.call(envelope)

    def test_http_error_does_not_expose_key_from_response(self):
        client = OpenAIComparison("test-key")
        error = HTTPError("https://api.openai.com/v1/responses", 401, "test-key", {},
                          io.BytesIO(b'{"error": {"message": "invalid key: test-key"}}'))
        with patch("scripts.compare_openai.urlopen", side_effect=error):
            with self.assertRaises(QQError) as raised:
                client("test-model", "ignored", {}, "Review code", {})
        self.assertNotIn("test-key", str(raised.exception))
