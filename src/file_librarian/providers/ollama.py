"""Local models through Ollama (M3, issue #33).

A preset of ``openai-compat``: Ollama serves the same Chat Completions API at
``http://localhost:11434/v1`` (``provider.base_url`` changes it), and the
request, validation, error handling and vision support are the same.

What differs:

- No API key is ever resolved or sent.
- Errors name Ollama and say how to fix the usual causes (not running, model
  not pulled).
- ``local`` is true when ``base_url`` is a loopback address (the default).
  That only removes the requests-per-minute spacing, since nothing is rate
  limited on your own Mac. It relaxes nothing about what is sent:
  sensitive files and ``never_send`` matches are withheld as with any other
  provider. Pointed at another machine, ``local`` is false and
  ``flib check-config`` warns that file content leaves this Mac.
- Cost: prices default to 0, which for a local model is the truth; the cost
  cap therefore never stops a run.

``installed_models`` is what ``flib check-config`` and ``flib init`` use to see
whether Ollama is running and the model is pulled: one ``GET /models``, with no
file content.
"""

from __future__ import annotations

import urllib.error

from file_librarian.config import OLLAMA_BASE_URL, ProviderConfig
from file_librarian.providers.base import FatalProviderError, ProviderError
from file_librarian.providers.openai_compat import OpenAICompatProvider

DEFAULT_MODEL = "llama3.2"
PULL_HINT = "run `ollama pull {model}`"


class OllamaProvider(OpenAICompatProvider):
    name = "ollama"
    label = "Ollama"

    def _resolve_key(self) -> str | None:
        return None  # Ollama takes no key; never send one

    def _status_error(self, exc: urllib.error.HTTPError) -> ProviderError:
        if exc.code == 404:
            model = self._config.model
            return FatalProviderError(
                f"Ollama answered 404 at {self._base_url}: check provider.base_url, and that "
                f"model {model!r} is pulled ({PULL_HINT.format(model=model)})"
            )
        return super()._status_error(exc)


def installed_models(base_url: str = OLLAMA_BASE_URL, *, timeout: float = 3.0) -> list[str]:
    """The models Ollama at ``base_url`` has pulled; raises ``ProviderError`` if unreachable."""
    probe = OllamaProvider(ProviderConfig(name="ollama", model="-", base_url=base_url))
    return probe.list_models(timeout=timeout)


def has_model(models: list[str], model: str) -> bool:
    """``model`` is pulled; a name without a tag means ``:latest``, as in ``ollama run``."""
    wanted = model if ":" in model else f"{model}:latest"
    return model in models or wanted in models


def check(config: ProviderConfig) -> tuple[bool, str]:
    """Whether Ollama answers and has the model, with a line explaining it. No file is sent."""
    url = config.base_url or OLLAMA_BASE_URL
    try:
        models = OllamaProvider(config).list_models()
    except ProviderError as exc:
        return False, f"{exc}; is Ollama running? Start it with `ollama serve`"
    if not has_model(models, config.model):
        return (
            False,
            f"Ollama has no model {config.model!r}; {PULL_HINT.format(model=config.model)}",
        )
    return True, f"Ollama at {url} has {config.model!r}"
