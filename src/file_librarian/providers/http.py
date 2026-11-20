"""JSON over HTTP to a configured server, shared by ``openai-compat`` and embeddings.

- Where it goes: only to the ``base_url`` the user configured. Redirects are
  refused, not followed, so a server cannot send the request on to another
  address; a loopback ``base_url`` also bypasses any HTTP proxy, so traffic
  meant for this Mac never leaves it.
- Answers are read up to ``MAX_RESPONSE_BYTES`` and must be a JSON object.
- An HTTP status error is raised as the ``urllib.error.HTTPError`` itself, for
  the caller to word; transport failures (timeouts, refused connections) become
  a retryable ``ProviderError`` naming ``label``. TLS certificate failures are
  fatal.

Only the standard library (``urllib``) is used, so this adds no dependency.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from http.client import HTTPException
from typing import Any

from file_librarian import __version__
from file_librarian.config import is_loopback_url
from file_librarian.providers.base import FatalProviderError, ProviderError

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: the request (and what is in it) goes to ``base_url`` only."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def build_opener(base_url: str) -> urllib.request.OpenerDirector:
    """An opener that never follows redirects and never proxies loopback traffic."""
    handlers: list[urllib.request.BaseHandler] = [_NoRedirect()]
    if is_loopback_url(base_url):
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


class JsonClient:
    """JSON requests to one ``base_url``, with an optional bearer key."""

    def __init__(
        self,
        base_url: str,
        *,
        label: str,
        key: str | None,
        timeout: float,
        opener: urllib.request.OpenerDirector | None = None,
    ) -> None:
        self.base_url = base_url
        self.label = label
        self._key = key
        self._timeout = timeout
        self._opener = opener or build_opener(base_url)

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None, *, timeout: float = 0
    ) -> dict[str, Any]:
        """One request to ``base_url + path``; raises ``HTTPError`` or ``ProviderError``."""
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
        url = self.base_url + path
        request = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310 - http(s) only, checked by config
        try:
            with self._opener.open(request, timeout=timeout or self._timeout) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError:
            raise
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

    def _connection_error(self, reason: object) -> ProviderError:
        if isinstance(reason, ssl.SSLCertVerificationError):
            return FatalProviderError(
                f"{self.label}'s TLS certificate could not be verified: {reason.verify_message}"
            )
        if isinstance(reason, TimeoutError):
            return ProviderError(f"{self.label} timed out", retryable=True)
        return ProviderError(
            f"cannot reach {self.label} at {self.base_url}: {type(reason).__name__}"
            if isinstance(reason, BaseException)
            else f"cannot reach {self.label} at {self.base_url}",
            retryable=True,
        )


def error_detail(exc: urllib.error.HTTPError) -> str:
    """``: <message>`` from an OpenAI-style error body, short; empty when there is none."""
    try:
        body = json.loads(exc.read(64 * 1024) or b"null")
    except (ValueError, OSError):
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else error
    return f": {str(message)[:200]}" if message else ""
