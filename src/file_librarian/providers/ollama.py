"""Local models through Ollama (M3, issue #33; made sturdy for small models in #92).

Ollama has two APIs: an OpenAI-compatible one at ``http://localhost:11434/v1``
(``provider.base_url``) and its own at the same address without ``/v1``. This
provider lists models through the first, like ``openai-compat``, and
classifies through the second, ``POST /api/chat``, because only that one
takes what a small model on a laptop needs:

- ``options.num_ctx``: Ollama's default context (4096 tokens) is shorter than
  flib's prompt with a long excerpt, and it would silently cut the *start* of
  the prompt (the instructions). The context is sized to the request
  (``context_tokens``: at least ``MIN_CONTEXT``, at most ``MAX_CONTEXT``) and
  the excerpt is shortened if even that is not enough (``fit``).
- ``options.temperature: 0`` (and ``num_predict``, a cap on the answer's
  length, so a model that loops is stopped): the same file gets the same answer.
- ``think: false``: models that think by default (Qwen 3.5) would spend
  minutes on a one-line answer. Dropped for the run if a model refuses it.
- ``format``: the answer's JSON schema, where the model supports structured
  output. Some builds do not (the MLX builds Ollama pulls by default on Apple
  silicon answer HTTP 501, "structured output is unavailable"): then the
  schema goes into the instructions instead, for the rest of the run.

Every answer is then checked as strictly as with any provider, after two
repairs that cost nothing: near-valid JSON (text around the object, a code
fence, a trailing comma) is cut down to the object (``repair_json``), and a
category that differs from one in the list only by case, accents or spacing
is taken as that one. If the answer is still unusable (not JSON, or a
category outside the list), the model is asked once more, with its answer
and a one-line correction; a second bad answer fails the file as before.
``answers`` counts each of these, for ``flib eval``.

Also:

- No API key is ever resolved or sent.
- Errors name Ollama and say how to fix the usual causes: not running
  (``ollama serve`` or the app), model not pulled (``ollama pull <model>``).
- ``provider.timeout_seconds`` defaults to ``LOCAL_TIMEOUT`` here: the first
  request also loads the model into memory, and a laptop is slower than an API.
- ``local`` is true when ``base_url`` is a loopback address (the default).
  That only removes the requests-per-minute spacing, since nothing is rate
  limited on your own Mac. It relaxes nothing about what is sent:
  sensitive files and ``never_send`` matches are withheld as with any other
  provider. Pointed at another machine, ``local`` is false and
  ``flib check-config`` warns that file content leaves this Mac.
- Cost: prices default to 0, which for a local model is the truth; the cost
  cap therefore never stops a run.

None of this touches the hosted providers: their requests and prompt are
unchanged (``tests/unit/test_prompt_payload.py``).

``installed_models`` is what ``flib check-config`` and ``flib init`` use to see
whether Ollama is running and the model is pulled: one ``GET /models``, with no
file content.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import re
import shutil
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from typing import Any

from pydantic import ValidationError

from file_librarian.config import OLLAMA_BASE_URL, ProviderConfig
from file_librarian.i18n import t
from file_librarian.models import Classification, FileContext, Rules
from file_librarian.providers.base import FatalProviderError, ProviderError, Usage
from file_librarian.providers.http import RETRYABLE_STATUS, JsonClient, error_detail
from file_librarian.providers.openai_compat import (
    OpenAICompatProvider,
    json_object_instructions,
    strip_wrapping,
)
from file_librarian.providers.prompt import (
    normalize_answer,
    response_schema,
    system_prompt,
    user_message,
)

DEFAULT_MODEL = "llama3.2"
PULL_HINT = "run `ollama pull {model}`"
INSTALL_HINT = (
    "install Ollama with `brew install ollama` (then `brew services start ollama`) or the "
    "app from https://ollama.com/download, and open it"
)
LOCAL_TIMEOUT = 300.0
"""Seconds per request when ``provider.timeout_seconds`` is not set: loading a model
into memory and reading a long prompt on a laptop can take a minute or two."""
MIN_CONTEXT = 8192
MAX_CONTEXT = 32768
ANSWER_TOKENS = 1024
"""Room kept for the answer, and its cap (``num_predict``): a full answer is ~300."""
CHARS_PER_TOKEN = 3
"""A cautious estimate (real prompts run at 3.5-4 characters per token)."""
KEEP_ALIVE = "10m"
OLLAMA_FOLDERS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/Applications/Ollama.app/Contents/Resources",
)
"""Where Homebrew and the app put ``ollama``, for when it is not on PATH."""
CORRECTION = (
    "That answer could not be used: {problem}. Answer again with only the JSON object, "
    "every field present, and category exactly one of: {categories}."
)


class _Unusable(Exception):
    """The answer is not a usable classification; worth asking once more."""


def native_root(base_url: str) -> str:
    """Ollama's own API root: ``base_url`` without its ``/v1``."""
    return base_url.rstrip("/").removesuffix("/v1")


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN + 1


def context_tokens(prompt_chars: int) -> int:
    """``num_ctx`` for a prompt this long: room for the answer, in steps of 2048."""
    needed = prompt_chars // CHARS_PER_TOKEN + ANSWER_TOKENS
    rounded = -(-needed // 2048) * 2048
    return min(max(rounded, MIN_CONTEXT), MAX_CONTEXT)


def fit(context: FileContext, system: str) -> FileContext:
    """``context`` with its excerpt shortened so the request fits ``MAX_CONTEXT``."""
    if not context.text:
        return context
    budget = (MAX_CONTEXT - ANSWER_TOKENS) * CHARS_PER_TOKEN - len(system)
    over = len(system) + len(user_message(context)) - budget
    if over <= 0:
        return context
    keep = max(len(context.text) - over, 0)
    return dataclasses.replace(context, text=context.text[:keep] or None)


_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def repair_json(text: str) -> tuple[dict[str, Any], bool]:
    """The JSON object in ``text``, and whether it had to be repaired to read it.

    Repairs: a ``<think>`` block or code fence around it, text before or after
    the object, and trailing commas. Raises ``ValueError`` when no object can be read.
    """
    stripped = strip_wrapping(text)
    try:
        data = json.loads(stripped)
        repaired = stripped != text.strip()
    except ValueError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no JSON object in the answer") from None
        data = json.loads(_TRAILING_COMMA.sub(r"\1", stripped[start : end + 1]))
        repaired = True
    if not isinstance(data, dict):
        raise ValueError("the answer is not a JSON object")
    return data, repaired


def _fold(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(plain.casefold().split())


def match_category(value: object, allowed: list[str]) -> str | None:
    """The category in ``allowed`` that ``value`` names, ignoring case, accents and
    spacing; ``None`` when it names none."""
    if not isinstance(value, str):
        return None
    if value in allowed:
        return value
    folded = _fold(value)
    return next((name for name in allowed if _fold(name) == folded), None)


class OllamaProvider(OpenAICompatProvider):
    name = "ollama"
    label = "Ollama"

    def __init__(
        self, config: ProviderConfig, opener: urllib.request.OpenerDirector | None = None
    ) -> None:
        super().__init__(config, opener)
        chosen = "timeout_seconds" in config.model_fields_set
        timeout = config.timeout_seconds if chosen else LOCAL_TIMEOUT
        self._chat_client = JsonClient(
            native_root(self._base_url),
            label=self.label,
            key=None,
            timeout=timeout,
            opener=opener,
        )
        self._structured = True
        """Send ``format``; false once the model said it cannot."""
        self._think = True
        """Send ``think: false``; false once the model refused the field."""
        self.answers: Counter[str] = Counter()
        """How answers were obtained: ``repaired``, ``category_matched``, ``retried``,
        ``retry_failed``, ``unstructured`` (a request sent without ``format``)."""

    def _resolve_key(self) -> str | None:
        return None  # Ollama takes no key; never send one

    def _status_error(self, exc: urllib.error.HTTPError) -> ProviderError:
        if exc.code == 404:
            return self._not_found()
        return super()._status_error(exc)

    def _not_found(self) -> FatalProviderError:
        model = self._config.model
        return FatalProviderError(
            f"Ollama at {self._base_url} has no model {model!r}: "
            f"{PULL_HINT.format(model=model)} (or check provider.model and provider.base_url)"
        )

    # --- classify ----------------------------------------------------------------------

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        self.last_usage = Usage()
        system = system_prompt(rules)
        user = self._user_turn(fit(context, system))
        messages: list[dict[str, Any]] = [user]
        text = self._chat(system, messages, rules)
        try:
            return self._parse(text, rules)
        except _Unusable as exc:
            self.answers["retried"] += 1
            problem = str(exc)
        messages += [
            {"role": "assistant", "content": text[:4000]},
            {
                "role": "user",
                "content": CORRECTION.format(
                    problem=problem, categories=", ".join(c.name for c in rules.categories)
                ),
            },
        ]
        text = self._chat(system, messages, rules)
        try:
            return self._parse(text, rules)
        except _Unusable as exc:
            self.answers["retry_failed"] += 1
            raise ProviderError(f"invalid answer: {exc}"[:300]) from exc

    def _user_turn(self, context: FileContext) -> dict[str, Any]:
        turn: dict[str, Any] = {"role": "user", "content": user_message(context)}
        if context.attachment is not None:
            turn["images"] = [base64.standard_b64encode(context.attachment.data).decode("ascii")]
        return turn

    def _chat(self, system: str, messages: list[dict[str, Any]], rules: Rules) -> str:
        """One ``POST /api/chat``; adapts once to a model without structured output or
        thinking control, then returns the answer's text."""
        while True:  # each adaptation happens at most once, so this ends
            prompt = system if self._structured else system + json_object_instructions(rules)
            turns = [{"role": "system", "content": prompt}, *messages]
            chars = sum(len(str(t["content"])) for t in turns)
            body: dict[str, Any] = {
                "model": self._config.model,
                "messages": turns,
                "stream": False,
                "keep_alive": KEEP_ALIVE,
                "options": {
                    "temperature": 0,
                    "num_ctx": context_tokens(chars),
                    "num_predict": ANSWER_TOKENS,
                },
            }
            if self._think:
                body["think"] = False
            if self._structured:
                body["format"] = response_schema(rules)
            else:
                self.answers["unstructured"] += 1
            try:
                payload = self._chat_client.request("POST", "/api/chat", body)
            except urllib.error.HTTPError as exc:
                detail = error_detail(exc).lower()
                if self._structured and (exc.code == 501 or "structured output" in detail):
                    self._structured = False  # this model cannot: instructions instead
                    continue
                if self._think and exc.code == 400 and "think" in detail:
                    self._think = False  # this model takes no thinking control
                    continue
                raise self._chat_error(exc, detail) from None
            return self._answer_text(payload)

    def _chat_error(self, exc: urllib.error.HTTPError, detail: str) -> ProviderError:
        if exc.code == 404:
            return self._not_found()
        if exc.code in RETRYABLE_STATUS:
            return ProviderError(f"temporary error from Ollama: HTTP {exc.code}", retryable=True)
        return ProviderError(f"Ollama answered HTTP {exc.code}{detail}"[:300])

    def _answer_text(self, payload: dict[str, Any]) -> str:
        usage = self.last_usage
        usage.input_tokens += _count(payload.get("prompt_eval_count"))
        usage.output_tokens += _count(payload.get("eval_count"))
        usage.models.append(str(payload.get("model") or self._config.model))
        message = payload.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        text = content.strip() if isinstance(content, str) else ""
        if payload.get("done_reason") == "length":
            # Cut off: what came is unusable, but asking again (shorter) may work.
            return text + "\n(cut off: the answer was too long)"
        return text

    def _parse(self, text: str, rules: Rules) -> Classification:
        if not text:
            raise _Unusable("it was empty")
        try:
            data, repaired = repair_json(text)
        except ValueError as exc:
            raise _Unusable(f"it was not one JSON object ({exc})") from exc
        if repaired:
            self.answers["repaired"] += 1
        allowed = [c.name for c in rules.categories]
        category = match_category(data.get("category"), allowed)
        if category is None:
            raise _Unusable(f"category {data.get('category')!r} is not in the list")
        if category != data.get("category"):
            self.answers["category_matched"] += 1
            data["category"] = category
        try:
            return Classification.model_validate(normalize_answer(data))
        except (ValueError, TypeError, ValidationError) as exc:
            raise _Unusable(f"it did not match the schema ({_first_error(exc)})") from exc


def _count(value: object) -> int:
    return value if isinstance(value, int) and value > 0 else 0


def _first_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        error = exc.errors()[0]
        where = ".".join(str(p) for p in error["loc"])
        return f"{where}: {error['msg']}"[:120]
    return str(exc)[:120]


# --- Setup checks ------------------------------------------------------------------------


def installed_models(base_url: str = OLLAMA_BASE_URL, *, timeout: float = 3.0) -> list[str]:
    """The models Ollama at ``base_url`` has pulled; raises ``ProviderError`` if unreachable."""
    probe = OllamaProvider(ProviderConfig(name="ollama", model="-", base_url=base_url))
    return probe.list_models(timeout=timeout)


def has_model(models: list[str], model: str) -> bool:
    """``model`` is pulled; a name without a tag means ``:latest``, as in ``ollama run``."""
    wanted = model if ":" in model else f"{model}:latest"
    return model in models or wanted in models


def ollama_command() -> str | None:
    """The ``ollama`` executable on PATH (or in Homebrew's folders), if installed."""
    found = shutil.which("ollama")
    if found:
        return found
    for folder in OLLAMA_FOLDERS:
        candidate = shutil.which("ollama", path=folder)
        if candidate:
            return candidate
    return None


def check(config: ProviderConfig) -> tuple[bool, str]:
    """Whether Ollama answers and has the model, with a line explaining it. No file is sent."""
    url = config.base_url or OLLAMA_BASE_URL
    try:
        models = OllamaProvider(config).list_models()
    except ProviderError as exc:
        return False, t("term.ollama.down", error=str(exc))
    if not has_model(models, config.model):
        hint = t("term.ollama.pull", model=config.model)
        return False, t("term.ollama.no_model", model=repr(config.model), hint=hint)
    return True, t("term.ollama.ready", url=url, model=repr(config.model))
