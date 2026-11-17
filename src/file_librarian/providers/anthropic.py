"""Claude through the Anthropic API (the default provider, M1).

- Structured output: the response must match a JSON schema built per request,
  with the configured categories as an ``enum``; ``Classification`` then
  validates it again.
- No sampling parameters (current Claude models reject them); repeatability
  comes from the schema.
- Refusals are checked before reading content. Where the model supports it,
  server-side ``fallbacks: "default"`` lets Anthropic retry a declined request
  on its recommended fallback model.
- File content is untrusted: it goes in a delimited block, and the system
  prompt says it is data to describe, never instructions.
- Vision (opt-in, ``vision.enabled``): an attached image goes as an ``image``
  block and a scanned PDF's first pages as a ``document`` block, both base64,
  before the text, which then says they are the file's content and equally
  untrusted. Their tokens are part of the billed usage like any other input.
- Errors are split: authentication, permission and unknown-model errors are
  ``FatalProviderError`` (stop the run, mark nothing failed); rate limits,
  overload, timeouts and connection errors are retryable; anything else fails
  only this file.

The API key comes from ``credentials.resolve``: the environment, the macOS
Keychain or a configured ``.env`` file (``provider.api_key_source``), falling
back to the SDK's own resolution. It is never stored in the config file.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import anthropic
from pydantic import ValidationError

from file_librarian import credentials
from file_librarian.config import ProviderConfig
from file_librarian.models import Attachment, Classification, FileContext, Rules
from file_librarian.providers.base import FatalProviderError, ProviderError, Usage
from file_librarian.providers.prompt import (
    normalize_answer,
    response_schema,
    system_prompt,
    user_message,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept server-side `fallbacks: "default"`.
FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5", "claude-mythos-5", "claude-sonnet-5-5")
MAX_TOKENS = 4096  # thinking is always on for current models; leave it room

# US$ per million input / output tokens (Anthropic list prices, 2026-11).
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-2": (8.0, 40.0),
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
UNKNOWN_PRICE = (10.0, 50.0)  # assume the most expensive when unsure

# What arrives in ``FileContext.attachment``: images are sent as JPEG, scanned PDFs as PDF.
ATTACHMENTS = frozenset({"image/jpeg", "application/pdf"})


def attachment_block(attachment: Attachment) -> dict[str, Any]:
    """The image or document content block for an attachment, base64-encoded."""
    kind = "document" if attachment.media_type == "application/pdf" else "image"
    return {
        "type": kind,
        "source": {
            "type": "base64",
            "media_type": attachment.media_type,
            "data": base64.standard_b64encode(attachment.data).decode("ascii"),
        },
    }


def user_content(context: FileContext) -> str | list[dict[str, Any]]:
    """The user turn: the text alone, or the attachment followed by the text."""
    text = user_message(context)
    if context.attachment is None:
        return text
    return [attachment_block(context.attachment), {"type": "text", "text": text}]


class AnthropicProvider:
    name = "anthropic"
    local = False
    attachments = ATTACHMENTS

    def __init__(self, config: ProviderConfig, client: Any | None = None) -> None:
        self._config = config
        if client is None:
            key = credentials.resolve(config).value
            # Retries and timeouts are owned by the classifier, so the SDK must not retry too.
            if key:
                client = anthropic.Anthropic(
                    api_key=key, timeout=config.timeout_seconds, max_retries=0
                )
            else:
                # No key found: the SDK's own resolution still applies (e.g. `ant auth login`).
                client = anthropic.Anthropic(timeout=config.timeout_seconds, max_retries=0)
        self._client = client
        self.last_usage = Usage()

    def cost_usd(self, usage: Usage) -> float:
        """Priced at the most expensive model that ran (fallbacks can switch models)."""
        models = usage.models or [self._config.model]
        price_in, price_out = max(PRICES.get(m, UNKNOWN_PRICE) for m in models)
        return (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1_000_000

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        cfg = self._config
        request: dict[str, Any] = {
            "model": cfg.model,
            "max_tokens": MAX_TOKENS,
            "system": system_prompt(rules),
            "messages": [{"role": "user", "content": user_content(context)}],
            "output_config": {
                "effort": cfg.effort,
                "format": {"type": "json_schema", "schema": response_schema(rules)},
            },
        }
        if cfg.model.startswith(FALLBACK_MODELS):
            request["betas"] = [FALLBACK_BETA]
            request["fallbacks"] = "default"
        try:
            response = self._client.beta.messages.create(**request)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise FatalProviderError(
                "Anthropic rejected the credentials; set ANTHROPIC_API_KEY or run `ant auth login`"
            ) from exc
        except anthropic.NotFoundError as exc:
            raise FatalProviderError(f"model {cfg.model!r} was not found") from exc
        except (
            anthropic.RateLimitError,
            anthropic.InternalServerError,
            anthropic.APITimeoutError,
            anthropic.APIConnectionError,
        ) as exc:
            raise ProviderError(
                f"temporary API error: {type(exc).__name__}", retryable=True
            ) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"API error {exc.status_code}: {exc.message}"[:300]) from exc
        except anthropic.AnthropicError as exc:
            # Raised before any request was made, e.g. no credentials could be resolved.
            raise FatalProviderError(f"Anthropic client error: {exc}"[:300]) from exc

        self.last_usage = _usage(response)
        if response.stop_reason == "refusal":
            raise ProviderError("the model declined to classify this file")
        if response.stop_reason == "max_tokens":
            raise ProviderError("the answer was cut off (max_tokens)")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            raise ProviderError("the answer had no text")
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            return Classification.model_validate(normalize_answer(data))
        except (ValueError, ValidationError) as exc:
            raise ProviderError(f"invalid answer: {exc}"[:300]) from exc


def _usage(response: Any) -> Usage:
    iterations = getattr(response.usage, "iterations", None) or []
    if iterations:
        return Usage(
            sum(getattr(i, "input_tokens", 0) or 0 for i in iterations),
            sum(getattr(i, "output_tokens", 0) or 0 for i in iterations),
            [str(getattr(i, "model", "") or "") for i in iterations],
        )
    return Usage(response.usage.input_tokens or 0, response.usage.output_tokens or 0, [])
