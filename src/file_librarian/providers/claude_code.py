"""Claude through the Claude Code CLI (``claude -p``), on the user's subscription.

For people who already use Claude Code and have no API key. Each file is one
non-interactive ``claude`` call:

- **Only classification.** ``--tools ""`` removes every built-in tool,
  ``--strict-mcp-config`` (with no ``--mcp-config``) loads no MCP server, and
  ``--safe-mode`` skips CLAUDE.md, hooks, skills, plugins and auto memory. The
  call runs in an empty temporary folder, so no project settings apply either;
  ``--permission-mode dontAsk`` denies anything that would still ask.
- **Structured output.** ``--output-format json --json-schema`` with the same
  schema as the ``anthropic`` provider (categories as an ``enum``); the answer
  arrives in ``structured_output`` and ``Classification`` validates it again.
- **The prompt goes through stdin**, the system prompt replaces Claude Code's
  own (``--system-prompt``), and ``--no-session-persistence`` keeps file
  content out of Claude Code's transcripts.
- **A minimal environment.** Only ``HOME``, ``USER``, ``LOGNAME``, ``TMPDIR``,
  the locale and ``CLAUDE_CONFIG_DIR`` pass through; ``ANTHROPIC_API_KEY`` and
  every other secret in the caller's environment do not. Claude Code's own
  retries are off (``CLAUDE_CODE_MAX_RETRIES=0``): the classifier owns them.
- **Cost.** The JSON result reports ``total_cost_usd``, Claude Code's own
  estimate at API list prices. ``limits.max_cost_usd_per_run`` applies to that
  figure; on a subscription nothing is billed per call, but the figure still
  tracks how much of the plan's usage a run takes.
- **No attachments.** ``claude -p`` takes the prompt as text; images and
  scanned PDFs are classified from their metadata, as with vision off.

The executable is ``provider.command`` when set; otherwise ``claude`` on PATH,
then the usual install folders, because a scheduled run (launchd) has a
minimal PATH (``scheduler.SAFE_PATH``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from file_librarian.config import ProviderConfig
from file_librarian.models import Classification, FileContext, Rules
from file_librarian.providers.base import FatalProviderError, ProviderError, Usage
from file_librarian.providers.prompt import (
    normalize_answer,
    response_schema,
    system_prompt,
    user_message,
)
from file_librarian.scheduler import SAFE_PATH

# Where the native installer, the old local install and Homebrew put ``claude``.
INSTALL_LOCATIONS = (
    "~/.local/bin/claude",
    "~/.claude/local/claude",
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
)
# The only variables the ``claude`` process inherits from flib's environment.
PASSED_ENV = ("HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "CLAUDE_CONFIG_DIR")
FIXED_ENV = {
    "CLAUDE_CODE_MAX_RETRIES": "0",  # the classifier retries, with its own backoff
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",  # no auto-update or telemetry per file
}
# Used only when the result reports tokens but no cost: the most expensive list price.
FALLBACK_PRICE = (10.0, 50.0)

LOGIN_HINT = "run `claude` once and sign in with /login (or `claude auth login`)"
AUTH_SIGNS = (
    "not logged in",
    "/login",
    "authentication",
    "invalid api key",
    "oauth token",
    "does not have access to claude",
)
LIMIT_SIGNS = ("hit your", "usage limit", "spend limit", "credit balance")
TRANSIENT_SIGNS = ("overloaded", "high load", "timed out", "no response", "temporarily", "529")


def find_claude(config: ProviderConfig, search_path: str | None = None) -> Path | None:
    """The ``claude`` executable to run, or None when there is none.

    ``search_path`` is the PATH to look in (the current one by default); the
    install folders are tried after it, so a launchd job finds the usual installs.
    """
    if config.command is not None:
        return config.command if _executable(config.command) else None
    found = shutil.which("claude", path=search_path)
    if found:
        return Path(found)
    for candidate in INSTALL_LOCATIONS:
        path = Path(candidate).expanduser()
        if _executable(path):
            return path
    return None


def _executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def logged_in(executable: Path, timeout: float = 30.0) -> bool | None:
    """Whether Claude Code has a login, from ``claude auth status``; None if it cannot tell.

    It runs with the same minimal environment as a classification, so an
    ``ANTHROPIC_API_KEY`` in this shell does not count as a login.
    """
    try:
        done = subprocess.run(  # noqa: S603 - fixed arguments, no shell
            [str(executable), "auth", "status", "--json"],
            capture_output=True,
            text=True,
            env=child_env(executable),
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.returncode == 0


def command_line(config: ProviderConfig, executable: Path, rules: Rules) -> list[str]:
    """The full ``claude`` invocation; the user message goes through stdin."""
    return [
        str(executable),
        "--print",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(response_schema(rules), ensure_ascii=False),
        "--model",
        config.model,
        "--effort",
        config.effort,
        "--system-prompt",
        system_prompt(rules),
        "--tools",
        "",
        "--strict-mcp-config",
        "--safe-mode",
        "--permission-mode",
        "dontAsk",
        "--no-session-persistence",
    ]


def child_env(executable: Path, environ: dict[str, str] | None = None) -> dict[str, str]:
    """The environment for ``claude``: an allow-list, never the caller's secrets.

    PATH starts with the executable's own folder so an npm install finds its
    ``node`` (usually next to it) even under launchd.
    """
    source = os.environ if environ is None else environ
    env = {name: source[name] for name in PASSED_ENV if source.get(name)}
    env["PATH"] = f"{executable.parent}:{SAFE_PATH}"
    env.update(FIXED_ENV)
    return env


class ClaudeCodeProvider:
    name = "claude-code"
    local = False
    attachments: frozenset[str] = frozenset()  # `claude -p` takes text only

    def __init__(self, config: ProviderConfig, executable: Path | None = None) -> None:
        self._config = config
        found = executable or find_claude(config)
        if found is None:
            where = (
                f"provider.command ({config.command}) is not an executable file"
                if config.command is not None
                else "`claude` was not found on PATH or in "
                + ", ".join(INSTALL_LOCATIONS)
                + "; install Claude Code or set provider.command"
            )
            raise FatalProviderError(f"Claude Code is not available: {where}")
        self.executable = found
        self.last_usage = Usage()

    def cost_usd(self, usage: Usage) -> float:
        """Claude Code's own estimate when it reported one, else a conservative price."""
        if usage.cost_usd is not None:
            return usage.cost_usd
        price_in, price_out = FALLBACK_PRICE
        return (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1_000_000

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        args = command_line(self._config, self.executable, rules)
        with tempfile.TemporaryDirectory(prefix="flib-claude-") as workdir:
            try:
                done = subprocess.run(  # noqa: S603 - fixed arguments, no shell
                    args,
                    # Nothing is attached, so the message must not say there is.
                    input=user_message(replace(context, attachment=None)),
                    capture_output=True,
                    text=True,
                    cwd=workdir,
                    env=child_env(self.executable),
                    timeout=self._config.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ProviderError(
                    f"claude did not answer within {self._config.timeout_seconds:g}s",
                    retryable=True,
                ) from exc
            except OSError as exc:
                raise FatalProviderError(f"could not run {self.executable}: {exc}") from exc
        result = _result(done.stdout)
        if result is None:
            raise _failure_without_result(done)
        self.last_usage = _usage(result)
        if result.get("is_error") or result.get("subtype") != "success":
            raise _error(result)
        data = result.get("structured_output")
        if not isinstance(data, dict):
            raise ProviderError("the model gave no structured answer (it may have declined)")
        try:
            return Classification.model_validate(normalize_answer(data))
        except (ValueError, ValidationError) as exc:
            raise ProviderError(f"invalid answer: {exc}"[:300]) from exc


def _result(stdout: str) -> dict[str, Any] | None:
    """The ``result`` message from ``--output-format json``, or None if there is none."""
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    if isinstance(data, list):  # some versions print every message; the result is last
        data = next((m for m in reversed(data) if isinstance(m, dict)), None)
    if isinstance(data, dict) and data.get("type") == "result":
        return data
    return None


def _failure_without_result(done: subprocess.CompletedProcess[str]) -> ProviderError:
    detail = (done.stderr or done.stdout or "").strip()[:300] or "no output"
    lowered = detail.lower()
    if any(sign in lowered for sign in AUTH_SIGNS):
        return FatalProviderError(f"Claude Code is not logged in; {LOGIN_HINT} ({detail})")
    if "unknown option" in lowered or "unknown argument" in lowered:
        return FatalProviderError(
            f"this Claude Code is too old for flib; run `claude update` ({detail})"
        )
    if done.returncode != 0:
        return ProviderError(f"claude exited with code {done.returncode}: {detail}")
    return ProviderError(f"claude printed no result: {detail}")


def _error(result: dict[str, Any]) -> ProviderError:
    errors = result.get("errors")
    detail = str(result.get("result") or "; ".join(map(str, errors or [])) or result.get("subtype"))
    detail = detail.strip()[:300]
    lowered = detail.lower()
    status = result.get("api_error_status")
    if status in (401, 403) or any(sign in lowered for sign in AUTH_SIGNS):
        return FatalProviderError(f"Claude Code is not logged in; {LOGIN_HINT} ({detail})")
    if any(sign in lowered for sign in LIMIT_SIGNS):
        # A subscription limit resets in hours: retrying now only fails every file.
        return FatalProviderError(f"Claude usage limit reached: {detail}")
    if status == 404 or ("model" in lowered and "not found" in lowered):
        return FatalProviderError(f"Claude Code could not use this model: {detail}")
    if status == 429 or (isinstance(status, int) and status >= 500):
        return ProviderError(f"temporary API error {status}: {detail}", retryable=True)
    if any(sign in lowered for sign in TRANSIENT_SIGNS):
        return ProviderError(f"temporary error: {detail}", retryable=True)
    return ProviderError(f"claude failed: {detail}")


def _usage(result: dict[str, Any]) -> Usage:
    """Tokens summed over every model the call used, with Claude Code's cost estimate."""
    per_model = result.get("modelUsage")
    cost = result.get("total_cost_usd")
    reported = float(cost) if isinstance(cost, int | float) else None
    if isinstance(per_model, dict) and per_model:
        counts = [m for m in per_model.values() if isinstance(m, dict)]
        return Usage(
            sum(
                int(m.get(k) or 0)
                for m in counts
                for k in ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
            ),
            sum(int(m.get("outputTokens") or 0) for m in counts),
            [str(name) for name in per_model],
            reported,
        )
    usage = result.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    return Usage(
        sum(
            int(usage.get(k) or 0)
            for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        ),
        int(usage.get("output_tokens") or 0),
        [],
        reported,
    )
