"""Replay saved PR evidence through OpenAI, keeping qq's validation checks."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from qq.core import QQError, analyse, save_json


class OpenAIComparison:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.calls = []

    def __call__(self, model, endpoint, schema, system, evidence):
        body = {
            "model": model, "store": False, "max_output_tokens": 8192,
            "reasoning": {"effort": "medium"},
            "instructions": system, "input": json.dumps(evidence),
            "text": {"format": {"type": "json_schema", "name": "qq_result",
                                "strict": True, "schema": schema}},
        }
        request = Request("https://api.openai.com/v1/responses", json.dumps(body).encode(),
                          {"Authorization": "Bearer " + self.api_key,
                           "Content-Type": "application/json"})
        started = time.monotonic()
        try:
            with urlopen(request, timeout=300) as response:
                envelope = json.load(response)
        except HTTPError as exc:
            # Authentication errors may echo part of a key. Do not print the body.
            raise QQError(f"OpenAI returned HTTP {exc.code}. Check API access, billing, "
                          "and model availability.") from exc
        except (URLError, OSError, ValueError) as exc:
            raise QQError("Could not read a response from the OpenAI API.") from exc
        self.calls.append({"id": envelope.get("id"), "model": envelope.get("model"),
                           "status": envelope.get("status"), "usage": envelope.get("usage"),
                           "elapsed_seconds": round(time.monotonic() - started, 2)})
        if envelope.get("status") != "completed":
            reason = (envelope.get("incomplete_details") or {}).get("reason", "unknown")
            raise QQError(f"OpenAI did not complete the response: {reason}.")
        text = []
        for item in envelope.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "refusal":
                    raise QQError("OpenAI declined the analysis. No feedback was accepted.")
                if content.get("type") == "output_text":
                    text.append(content["text"])
        try:
            return json.loads("".join(text))
        except (ValueError, TypeError) as exc:
            raise QQError("OpenAI did not return a usable JSON result.") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6.1-sol")
    args = parser.parse_args()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        parser.error("Set OPENAI_API_KEY before running the comparison.")
    if args.output.exists():
        parser.error("The output file already exists. Choose a new path.")
    packet = json.loads(args.packet.read_text())
    client = OpenAIComparison(api_key)
    started = time.monotonic()
    review = {"packet": packet, "model": args.model, "provider": "openai",
              "created_at": datetime.now(timezone.utc).isoformat(), "assessment": {}}
    try:
        review["report"] = analyse(packet, args.model, "https://api.openai.com/v1",
                                   lambda message: print(message, flush=True),
                                   audit=review["assessment"], model_call=client)
    except QQError as exc:
        review["error"] = str(exc)
    review["elapsed_seconds"] = round(time.monotonic() - started, 2)
    review["api_calls"] = client.calls
    save_json(args.output, review)
    if "error" in review:
        print(f"Comparison failed: {review['error']} Diagnostic saved to {args.output}", file=sys.stderr)
        return 1
    print(json.dumps({"report": review["report"], "elapsed_seconds": review["elapsed_seconds"],
                      "api_calls": client.calls, "saved_to": str(args.output)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
