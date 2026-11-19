import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from qq.__main__ import main
from qq.providers import OPENAI_MODEL, OLLAMA_MODEL
from qq.tui import paragraphs


class ProviderCLITests(unittest.TestCase):
    def setUp(self):
        self.packet = {"id": "provider-test", "pr": {"headRefOid": "head"},
                       "merge_base": "base", "diff": "", "coverage_notes": [], "files": []}
        self.report = {"status": "no_feedback", "summary": "No worthwhile point.", "points": []}

    def run_cli(self, arguments=(), env=None, api_error=None):
        envelope = {"id": "response-1", "status": "completed", "model": OPENAI_MODEL,
                    "usage": {"input_tokens": 100, "output_tokens": 20},
                    "output": [{"type": "message", "content": [
                        {"type": "output_text", "text": json.dumps(self.report)}]}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packet = root / "input.json"
            packet.write_text(json.dumps(self.packet))
            stdout, stderr = io.StringIO(), io.StringIO()
            argv = ["qq", "--packet", str(packet), "--state-dir", str(root), "--json", *arguments]
            with patch.dict(os.environ, env if env is not None else {"OPENAI_API_KEY": "test-api-key"}, clear=True), \
                    patch("sys.argv", argv), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                    patch("qq.providers.urlopen", side_effect=api_error or (lambda *args, **kwargs: io.StringIO(json.dumps(envelope)))) as api, \
                    patch("qq.core.urlopen", side_effect=lambda *args, **kwargs: io.StringIO(json.dumps({"message": {"content": json.dumps(self.report)}}))) as local:
                code = main()
            return {"code": code, "output": json.loads(stdout.getvalue()) if stdout.getvalue() else None,
                    "stderr": stderr.getvalue(), "api": api, "local": local,
                    "reviews": [json.loads(p.read_text()) for p in root.glob("*.review.json")],
                    "failures": [json.loads(p.read_text()) for p in root.glob("*.failure.json")]}

    def test_normal_command_defaults_to_openai_and_records_provider_usage(self):
        result = self.run_cli(env={"OPENAI_API_KEY": "test-api-key", "QQ_MODEL": "legacy-local-model"})
        self.assertEqual(result["code"], 0)
        result["local"].assert_not_called()
        self.assertEqual(result["api"].call_args.args[0].full_url, "https://api.openai.com/v1/responses")
        body = json.loads(result["api"].call_args.args[0].data)
        self.assertEqual(body["model"], OPENAI_MODEL)
        review = result["reviews"][0]
        self.assertEqual(review["provider"], "openai")
        self.assertEqual(review["model"], OPENAI_MODEL)
        self.assertEqual(review["api_calls"][0]["usage"]["input_tokens"], 100)
        self.assertIn("Selected PR evidence will be sent to OpenAI", result["stderr"])
        self.assertNotIn("test-api-key", json.dumps(result["output"]))
        self.assertIn(f"Model: {OPENAI_MODEL} via OpenAI API", paragraphs(review, 0, 3))

    def test_missing_api_key_fails_without_local_fallback(self):
        result = self.run_cli(env={})
        self.assertEqual(result["code"], 1)
        result["api"].assert_not_called()
        result["local"].assert_not_called()
        self.assertIn("OPENAI_API_KEY is not set", result["stderr"])
        self.assertEqual(result["reviews"], [])

    def test_explicit_ollama_works_without_api_key(self):
        result = self.run_cli(["--provider", "ollama"], env={})
        self.assertEqual(result["code"], 0)
        result["api"].assert_not_called()
        self.assertEqual(json.loads(result["local"].call_args.args[0].data)["model"], OLLAMA_MODEL)
        self.assertEqual(result["output"]["provider"], "ollama")
        self.assertNotIn("api_calls", result["output"])

    def test_local_endpoint_and_model_settings_stay_local(self):
        result = self.run_cli(["--provider", "ollama", "--endpoint", "http://127.0.0.1:12400"],
                              env={"QQ_MODEL": "local-override"})
        result["api"].assert_not_called()
        sent = result["local"].call_args.args[0]
        self.assertEqual(sent.full_url, "http://127.0.0.1:12400/api/chat")
        self.assertEqual(json.loads(sent.data)["model"], "local-override")

    def test_endpoint_requires_explicit_local_provider(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_cli(["--endpoint", "http://127.0.0.1:12400"])
        self.assertEqual(raised.exception.code, 2)

    def test_openai_model_can_be_selected_in_environment_or_cli(self):
        for arguments, expected in [([], "env-model"), (["--model", "cli-model"], "cli-model")]:
            with self.subTest(arguments=arguments):
                result = self.run_cli(arguments, env={"OPENAI_API_KEY": "test-api-key", "QQ_OPENAI_MODEL": "env-model"})
                body = json.loads(result["api"].call_args.args[0].data)
                self.assertEqual(body["model"], expected)

    def test_collection_only_requires_no_model_or_api_key(self):
        result = self.run_cli(["--collect-only"], env={})
        self.assertEqual(result["code"], 0)
        result["api"].assert_not_called()
        result["local"].assert_not_called()
        self.assertEqual(result["output"], self.packet)

    def test_api_failure_saves_diagnostic_without_review_or_fallback(self):
        error = HTTPError("https://api.openai.com/v1/responses", 429, "quota", {}, None)
        result = self.run_cli(api_error=error)
        self.assertEqual(result["code"], 1)
        result["local"].assert_not_called()
        self.assertEqual(result["reviews"], [])
        failure = result["failures"][0]
        self.assertEqual(failure["provider"], "openai")
        self.assertEqual(failure["api_calls"][0]["http_status"], 429)
        self.assertIn("billing", failure["error"])

    def test_example_opens_without_api_key_or_network(self):
        with patch.dict(os.environ, {}, clear=True), patch("sys.argv", ["qq", "--example", "--json"]), \
                patch("qq.providers.urlopen") as api, patch("qq.core.urlopen") as local, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 0)
        api.assert_not_called()
        local.assert_not_called()
