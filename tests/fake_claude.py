"""A stand-in for the Claude Code CLI (``claude``) used by every test (see conftest.py).

The real ``claude`` is never run. This one keeps its state in the folder named
by FAKE_CLAUDE_DIR, which the shim sets itself because the provider passes
only an allow-list of environment variables:

- every call is appended to ``calls.jsonl``: argv, stdin, the environment
  variable names it received and the working directory;
- ``auth status`` exits 0 ("logged in") unless a file ``logged_out`` exists;
- otherwise it prints a successful ``--output-format json`` result whose
  ``structured_output`` picks the first category of the ``--json-schema``,
  unless ``reply.json`` exists: then it prints its ``stdout`` and ``stderr``,
  sleeps ``sleep`` seconds and exits with ``exit``.
"""

from __future__ import annotations

import json
import os
import sys
import time


def success(schema: dict[str, object]) -> dict[str, object]:
    properties = schema["properties"]
    assert isinstance(properties, dict)
    category = properties["category"]["enum"][0]
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "",
        "num_turns": 1,
        "total_cost_usd": 0.0123,
        "usage": {"input_tokens": 900, "output_tokens": 80},
        "modelUsage": {
            "claude-haiku-5-5": {
                "inputTokens": 900,
                "outputTokens": 80,
                "cacheReadInputTokens": 100,
                "cacheCreationInputTokens": 0,
                "costUSD": 0.0123,
            }
        },
        "structured_output": {
            "category": category,
            "suggested_name": "electricity invoice",
            "description": "An electricity invoice.",
            "tags": ["invoice"],
            "confidence": 0.9,
            "document_date": "2026-10-05",
        },
    }


def main(argv: list[str]) -> int:
    state = os.environ["FAKE_CLAUDE_DIR"]
    stdin = "" if argv[:2] == ["auth", "status"] else sys.stdin.read()
    call = {
        "argv": argv,
        "stdin": stdin,
        "env": sorted(k for k in os.environ if k != "FAKE_CLAUDE_DIR"),
        "path": os.environ.get("PATH", ""),
        "cwd": os.getcwd(),
    }
    with open(os.path.join(state, "calls.jsonl"), "a", encoding="utf-8") as log:
        log.write(json.dumps(call) + "\n")
    if argv[:2] == ["auth", "status"]:
        if os.path.exists(os.path.join(state, "logged_out")):
            print(json.dumps({"loggedIn": False, "authMethod": "none"}))
            return 1
        print(json.dumps({"loggedIn": True, "authMethod": "claude.ai"}))
        return 0
    reply_file = os.path.join(state, "reply.json")
    if os.path.exists(reply_file):
        with open(reply_file, encoding="utf-8") as fh:
            reply = json.load(fh)
        time.sleep(reply.get("sleep", 0))
        sys.stdout.write(reply.get("stdout", ""))
        sys.stderr.write(reply.get("stderr", ""))
        return int(reply.get("exit", 0))
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    print(json.dumps(success(schema)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
