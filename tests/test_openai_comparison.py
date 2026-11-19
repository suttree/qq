import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from qq.providers import OpenAIClient
from qq.core import QQError


class OpenAIClientTests(unittest.TestCase):
    def call(self, envelope):
        client = OpenAIClient("test-key")
        with patch("qq.providers.urlopen",
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
            None,
            {"status": "incomplete", "incomplete_details": {"reason": "content_filter"}},
            {"status": "completed", "output": {}},
            {"status": "completed", "output": [{"type": "message", "content": None}]},
            {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]},
            {"status": "completed", "output": []},
            {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "invalid"}]}]},
            {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "[]"}]}]},
        ]
        for envelope in envelopes:
            with self.subTest(envelope=envelope), self.assertRaises(QQError):
                self.call(envelope)

    def test_output_limit_retries_once_and_keeps_both_attempts(self):
        incomplete = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                      "output": [{"type": "message", "content": [{"type": "output_text", "text": "{"}]}]}
        completed = {"status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": '{"points": []}'}]}]}
        for second in (completed, incomplete):
            with self.subTest(status=second["status"]):
                client = OpenAIClient("test-key")
                responses = [io.StringIO(json.dumps(envelope)) for envelope in (incomplete, second)]
                with patch("qq.providers.urlopen", side_effect=responses) as request:
                    if second is completed:
                        self.assertEqual(client("test-model", "", {}, "Review code", {}), {"points": []})
                    else:
                        with self.assertRaisesRegex(QQError, "max_output_tokens"):
                            client("test-model", "", {}, "Review code", {})
                self.assertEqual(request.call_count, 2)
                self.assertEqual([call["max_output_tokens"] for call in client.calls], [8192, 16384])
                self.assertEqual(client.calls[0]["incomplete_reason"], "max_output_tokens")

    def test_http_error_does_not_expose_key_from_response(self):
        client = OpenAIClient("test-key")
        error = HTTPError("https://api.openai.com/v1/responses", 401, "test-key", {},
                          io.BytesIO(b'{"error": {"message": "invalid key: test-key"}}'))
        with patch("qq.providers.urlopen", side_effect=error):
            with self.assertRaises(QQError) as raised:
                client("test-model", "ignored", {}, "Review code", {})
        self.assertNotIn("test-key", str(raised.exception))
