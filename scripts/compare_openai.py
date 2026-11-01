"""Replay saved PR evidence through OpenAI, keeping qq's validation checks."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from qq.core import QQError, analyse, save_json
from qq.providers import OPENAI_MODEL, OpenAIClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default=OPENAI_MODEL)
    args = parser.parse_args()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        parser.error("Set OPENAI_API_KEY before running the comparison.")
    if args.output.exists():
        parser.error("The output file already exists. Choose a new path.")
    packet = json.loads(args.packet.read_text())
    client = OpenAIClient(api_key)
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
