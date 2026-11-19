"""OpenAI Responses API transport for qq's validated review pipeline."""
from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .core import QQError


OPENAI_MODEL = "gpt-6.1-sol"
OLLAMA_MODEL = "qwen3.5:9b-coding"


class OpenAIClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = (api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")).strip()
        if not self.api_key:
            raise QQError("OPENAI_API_KEY is not set. Set it to use the OpenAI API, "
                          "or use --provider ollama for local inference.")
        self.calls = []

    def __call__(self, model, endpoint, schema, system, evidence):
        body = {
            "model": model, "store": False, "max_output_tokens": 8192,
            "reasoning": {"effort": "medium"},
            "instructions": system, "input": json.dumps(evidence),
            "text": {"format": {"type": "json_schema", "name": "qq_result",
                                "strict": True, "schema": schema}},
        }
        envelope = self._request(model, body)
        if envelope.get("status") == "incomplete" and self._incomplete_reason(envelope) == "max_output_tokens":
            body["max_output_tokens"] = 16384
            body["instructions"] += (
                "\nKeep the JSON concise. Finish as soon as the required fields are filled. "
                "Use short, exact source quotes and omit repetition."
            )
            envelope = self._request(model, body)
        if envelope.get("status") != "completed":
            reason = self._incomplete_reason(envelope) or "unknown"
            raise QQError(f"OpenAI did not complete the response: {reason}.")
        text = []
        output = envelope.get("output", [])
        if not isinstance(output, list):
            raise QQError("OpenAI returned an invalid output list.")
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            contents = item.get("content", [])
            if not isinstance(contents, list):
                raise QQError("OpenAI returned invalid message content.")
            for content in contents:
                if not isinstance(content, dict):
                    raise QQError("OpenAI returned invalid message content.")
                if content.get("type") == "refusal":
                    raise QQError("OpenAI declined the analysis. No feedback was accepted.")
                if content.get("type") == "output_text":
                    text.append(content.get("text", ""))
        try:
            result = json.loads("".join(text))
        except (ValueError, TypeError) as exc:
            raise QQError("OpenAI did not return a usable JSON result.") from exc
        if not isinstance(result, dict):
            raise QQError("OpenAI did not return a JSON object.")
        return result


    @staticmethod
    def _incomplete_reason(envelope):
        details = envelope.get("incomplete_details")
        return details.get("reason") if isinstance(details, dict) else None

    def _request(self, model, body):
        request = Request("https://api.openai.com/v1/responses", json.dumps(body).encode(),
                          {"Authorization": "Bearer " + self.api_key,
                           "Content-Type": "application/json"})
        started = time.monotonic()
        try:
            with urlopen(request, timeout=300) as response:
                envelope = json.load(response)
        except HTTPError as exc:
            self.calls.append({"model": model, "http_status": exc.code,
                               "elapsed_seconds": round(time.monotonic() - started, 2)})
            # Authentication errors can echo part of a key. Never print the body.
            detail = {
                401: "The API key was rejected. Check OPENAI_API_KEY.",
                403: "The API key does not have permission to use this model.",
                404: f"The model {model} is unavailable to this API key. Check --model.",
                429: "The API rate limit or quota was reached. Check API billing and usage limits.",
            }.get(exc.code, "Check API access and service availability.")
            exc.close()
            raise QQError(f"OpenAI returned HTTP {exc.code}. {detail}") from exc
        except (URLError, OSError, ValueError) as exc:
            raise QQError("Could not read a response from the OpenAI API. Check your connection.") from exc
        if not isinstance(envelope, dict):
            raise QQError("OpenAI returned an invalid response.")
        self.calls.append({"id": envelope.get("id"), "model": envelope.get("model"),
                           "status": envelope.get("status"), "usage": envelope.get("usage"),
                           "incomplete_reason": self._incomplete_reason(envelope),
                           "max_output_tokens": body["max_output_tokens"],
                           "elapsed_seconds": round(time.monotonic() - started, 2)})
        return envelope
