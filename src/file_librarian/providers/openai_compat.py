"""Any server that speaks the OpenAI Chat Completions API (M3, issue #34).

OpenAI itself, OpenRouter, Groq, LM Studio, llama.cpp's server, vLLM and
Ollama (see ``providers/ollama.py``, a preset of this one) all accept
``POST {base_url}/chat/completions``.

- Where it goes: only to ``provider.base_url``, which the user configured.
  Redirects are refused, not followed, so a server cannot send the file on to
  another address; a loopback ``base_url`` also bypasses any HTTP proxy, so
  traffic meant for this Mac never leaves it.
- Key: from ``credentials.resolve`` (``provider.api_key_env``, else
  ``FLIB_OPENAI_COMPAT_API_KEY``; the Keychain account
  ``openai-compat-api-key``; ``provider.env_file``). Optional: without one no
  ``Authorization`` header is sent, which is what local servers expect.
- Structured output: ``response_format`` ``json_schema`` with the schema from
  ``providers/prompt.py`` (categories as an ``enum``). A server that rejects it
  (HTTP 400 or 422) is asked again at once with ``json_object`` and the schema
  in the system prompt; if that is accepted, ``json_object`` is used for the
  rest of the run. Either way the answer is validated strictly: a category
  outside the list or a malformed answer fails the file.
- No sampling parameters and no ``max_tokens``: servers disagree on them
  (``max_tokens`` vs ``max_completion_tokens``) and the schema bounds the answer.
- Errors: 401 and 403 (credentials), 404 (model or URL) and redirects are
  ``FatalProviderError``; 408, 409, 429 and 5xx, timeouts and connection
  errors are retryable; anything else fails only this file.
- Cost: ``usage.prompt_tokens`` and ``completion_tokens`` times
  ``provider.price_input_per_mtok`` and ``price_output_per_mtok``. Both default
  to 0 (unknown): then every file costs US$ 0 and ``limits.max_cost_usd_per_run``
  cannot stop the run; ``flib check-config`` warns about it, and a run's cost is
  recorded as unknown (``prices_known``) unless the server is on this Mac.
- Vision: images go as a ``data:`` URL ``image_url`` part only with
  ``provider.vision: true`` (and ``vision.enabled``). Scanned PDFs are never
  sent: the Chat Completions API has no portable way to send a PDF, so they are
  classified from their metadata.
- ``local``: true when ``base_url`` is a loopback address. That only drops the
  requests-per-minute spacing; ``never_send`` and sensitive files are withheld
  exactly as with a remote provider.

HTTP goes through the standard library (``urllib``), so this provider adds no
dependency.
"""

from __future__ import annotations

import base64
import json
import re
import ssl
import urllib.error
import urllib.request
from http.client import HTTPException
from typing import Any, Literal

from pydantic import ValidationError

from file_librarian import __version__, credentials
from file_librarian.config import ProviderConfig, is_loopback_url
from file_librarian.models import Attachment, Classification, FileContext, Rules
from file_librarian.providers.base import FatalProviderError, ProviderError, Usage
from file_librarian.providers.prompt import (
    normalize_answer,
    response_schema,
    system_prompt,
    user_message,
)

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
IMAGE = "image/jpeg"

Mode = Literal["json_schema", "json_object"]


class _FormatRejected(Exception):
    """The server answered 400/422 to a ``json_schema`` request."""

    def __init__(self, error: ProviderError) -> None:
        super().__init__(str(error))
        self.error = error


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: the request (and the file in it) goes to ``base_url`` only."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def build_opener(base_url: str) -> urllib.request.OpenerDirector:
    """An opener that never follows redirects and never proxies loopback traffic."""
    handlers: list[urllib.request.BaseHandler] = [_NoRedirect()]
    if is_loopback_url(base_url):
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def image_part(attachment: Attachment) -> dict[str, Any]:
    data = base64.standard_b64encode(attachment.data).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{attachment.media_type};base64,{data}"},
    }


def user_content(context: FileContext) -> str | list[dict[str, Any]]:
    """The user turn: the text alone, or the image followed by the text."""
    text = user_message(context)
    if context.attachment is None:
        return text
    return [image_part(context.attachment), {"type": "text", "text": text}]


def json_object_instructions(rules: Rules) -> str:
    """Appended to the system prompt when the server only takes ``json_object``."""
    schema = json.dumps(response_schema(rules), ensure_ascii=False)
    return (
        "\n\nAnswer with a single JSON object and nothing else, matching this JSON "
        f"schema exactly (every field required, no other fields):\n{schema}"
    )


def response_format(rules: Rules, mode: Mode) -> dict[str, Any]:
    if mode == "json_object":
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {"name": "classification", "strict": True, "schema": response_schema(rules)},
    }


class OpenAICompatProvider:
    name = "openai-compat"
    label = "the server"

    def __init__(
        self, config: ProviderConfig, opener: urllib.request.OpenerDirector | None = None
    ) -> None:
        if config.base_url is None:  # the configuration requires it; guard direct use
            raise FatalProviderError(f"{self.name} needs provider.base_url")
        self._config = config
        self._base_url = config.base_url
        self._key = self._resolve_key()
        self._opener = opener or build_opener(self._base_url)
        self._mode: Mode = "json_schema"
        self.local = is_loopback_url(self._base_url)
        self.attachments: frozenset[str] = frozenset({IMAGE}) if config.vision else frozenset()
        self.last_usage = Usage()

    def _resolve_key(self) -> str | None:
        return credentials.resolve(self._config).value

    @property
    def mode(self) -> Mode:
        """``json_schema`` until the server rejects it once, then ``json_object``."""
        return self._mode

    @property
    def prices_known(self) -> bool:
        """False when no price is configured for a remote server: its cost is unknown,
        not zero. A server on this Mac costs nothing."""
        cfg = self._config
        return self.local or cfg.price_input_per_mtok > 0 or cfg.price_output_per_mtok > 0

    def cost_usd(self, usage: Usage) -> float:
        if usage.cost_usd is not None:
            return usage.cost_usd
        cfg = self._config
        return (
            usage.input_tokens * cfg.price_input_per_mtok
            + usage.output_tokens * cfg.price_output_per_mtok
        ) / 1_000_000

    # --- classify ----------------------------------------------------------------------

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        self.last_usage = Usage()
        if self._mode == "json_object":
            return self._answer(self._complete(context, rules, "json_object"), rules)
        try:
            payload = self._complete(context, rules, "json_schema")
        except _FormatRejected as rejected:
            try:
                payload = self._complete(context, rules, "json_object")
            except ProviderError as exc:
                if exc.retryable or isinstance(exc, FatalProviderError):
                    raise
                # Rejected both ways: the request itself is the problem, not the format.
                raise rejected.error from exc
            self._mode = "json_object"  # accepted: keep it for the rest of the run
        return self._answer(payload, rules)

    def _complete(self, context: FileContext, rules: Rules, mode: Mode) -> dict[str, Any]:
        system = system_prompt(rules)
        if mode == "json_object":
            system += json_object_instructions(rules)
        body = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content(context)},
            ],
            "response_format": response_format(rules, mode),
        }
        payload = self._request(
            "POST", "/chat/completions", body, reject_format=mode == "json_schema"
        )
        usage = _dict(payload.get("usage"))
        self.last_usage.input_tokens += _int(usage.get("prompt_tokens"))
        self.last_usage.output_tokens += _int(usage.get("completion_tokens"))
        self.last_usage.models.append(str(payload.get("model") or self._config.model))
        return payload

    def _answer(self, payload: dict[str, Any], rules: Rules) -> Classification:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError("the answer had no choices")
        choice = _dict(choices[0])
        message = _dict(choice.get("message"))
        if message.get("refusal") or choice.get("finish_reason") == "content_filter":
            raise ProviderError("the model declined to classify this file")
        if choice.get("finish_reason") == "length":
            raise ProviderError("the answer was cut off (length)")
        text = _text(message.get("content"))
        if not text:
            raise ProviderError("the answer had no text")
        try:
            data = json.loads(_strip_wrapping(text))
            if not isinstance(data, dict):
                raise ValueError("not an object")
            allowed = [c.name for c in rules.categories]
            if data.get("category") not in allowed:
                raise ValueError(f"category {data.get('category')!r} is not one of {allowed}")
            return Classification.model_validate(normalize_answer(data))
        except (ValueError, TypeError, ValidationError) as exc:
            raise ProviderError(f"invalid answer: {exc}"[:300]) from exc

    # --- HTTP ----------------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout: float = 0,
        reject_format: bool = False,
    ) -> dict[str, Any]:
        """One JSON request to ``base_url + path``; errors become ``ProviderError``.

        With ``reject_format``, HTTP 400 and 422 raise ``_FormatRejected`` instead.
        """
        headers = {
            "Accept": "application/json",
            "User-Agent": f"file-librarian/{__version__}",
        }
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        url = self._base_url + path
        request = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310 - http(s) only, checked by config
        try:
            with self._opener.open(
                request, timeout=timeout or self._config.timeout_seconds
            ) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            error = self._status_error(exc)
            if reject_format and exc.code in (400, 422):
                raise _FormatRejected(error) from None
            raise error from None
        except urllib.error.URLError as exc:
            raise self._connection_error(exc.reason) from None
        except TimeoutError as exc:
            raise ProviderError(f"{self.label} timed out", retryable=True) from exc
        except (HTTPException, OSError) as exc:
            raise ProviderError(
                f"connection to {self.label} failed: {type(exc).__name__}", retryable=True
            ) from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ProviderError("the answer was too large")
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise ProviderError(f"{self.label} did not answer with JSON") from exc
        if not isinstance(payload, dict):
            raise ProviderError(f"{self.label} did not answer with a JSON object")
        return payload

    def _status_error(self, exc: urllib.error.HTTPError) -> ProviderError:
        status = exc.code
        detail = _error_detail(exc)
        if 300 <= status < 400:
            error: ProviderError = FatalProviderError(
                f"{self.label} redirected the request (HTTP {status}); redirects are not "
                "followed. Set provider.base_url to the final address."
            )
        elif status in (401, 403):
            error = FatalProviderError(
                f"{self.label} rejected the credentials (HTTP {status}); {self._key_hint()}"
            )
        elif status == 404:
            error = FatalProviderError(
                f"{self.label} answered 404: check provider.base_url ({self._base_url}) and "
                f"that model {self._config.model!r} exists{detail}"
            )
        elif status in RETRYABLE_STATUS:
            error = ProviderError(
                f"temporary error from {self.label}: HTTP {status}", retryable=True
            )
        else:
            error = ProviderError(f"{self.label} answered HTTP {status}{detail}"[:300])
        return error

    def _key_hint(self) -> str:
        spec = credentials.key_spec(self._config)
        if spec is None:
            return "this provider sends no key"
        return f"run `flib key set --provider {spec.provider}`, or set {spec.env_var}"

    def _connection_error(self, reason: object) -> ProviderError:
        if isinstance(reason, ssl.SSLCertVerificationError):
            return FatalProviderError(
                f"{self.label}'s TLS certificate could not be verified: {reason.verify_message}"
            )
        if isinstance(reason, TimeoutError):
            return ProviderError(f"{self.label} timed out", retryable=True)
        return ProviderError(
            f"cannot reach {self.label} at {self._base_url}: {type(reason).__name__}"
            if isinstance(reason, BaseException)
            else f"cannot reach {self.label} at {self._base_url}",
            retryable=True,
        )

    def list_models(self, *, timeout: float = 5.0) -> list[str]:
        """The model ids ``GET {base_url}/models`` lists. Sends no file content."""
        payload = self._request("GET", "/models", timeout=timeout)
        entries = payload.get("data")
        if not isinstance(entries, list):
            raise ProviderError(f"{self.label} did not list its models")
        return [str(e["id"]) for e in entries if isinstance(e, dict) and e.get("id")]


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: object) -> int:
    return value if isinstance(value, int) and value > 0 else 0


def _text(content: object) -> str:
    """The message text; some servers send a list of ``{"type": "text"}`` parts."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(
            str(p.get("text", ""))
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        ).strip()
    return ""


_THINK = re.compile(r"\A\s*<think>.*?</think>\s*", re.DOTALL)
_FENCE = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)


def _strip_wrapping(text: str) -> str:
    """Drop a leading ``<think>`` block and a Markdown code fence some local models add."""
    text = _THINK.sub("", text).strip()
    match = _FENCE.match(text)
    return match.group(1) if match else text


def _error_detail(exc: urllib.error.HTTPError) -> str:
    """``: <message>`` from an OpenAI-style error body, short; empty when there is none."""
    try:
        body = json.loads(exc.read(64 * 1024) or b"null")
    except (ValueError, OSError):
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else error
    return f": {str(message)[:200]}" if message else ""
