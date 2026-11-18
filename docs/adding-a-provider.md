# Adding an AI provider

A provider turns a `FileContext` into a `Classification`. The classification
code lives in `src/file_librarian/providers/`. Outside it, a new provider
touches only:

- `config.py`: its name in `PROVIDER_NAMES` (a test keeps it equal to the
  registry), and any setting of its own as a field of `ProviderConfig`;
- `credentials.py`, if it takes an API key: a `KeySpec` in `spec_for`, with its
  own environment variable and Keychain account;
- `cli.py` and `onboarding.py`, if `flib check-config`, `flib init` or
  `flib key` should know about it;
- the documentation (step 4).

The reference implementations: `anthropic.py` (an SDK), `claude_code.py` (a
CLI), `openai_compat.py` (plain HTTP with the standard library) and
`ollama.py` (a preset: a subclass that changes defaults and messages).

## 1. Write the module

Create `src/file_librarian/providers/<name>.py` (use `_` for `-` in the module name):

```python
from file_librarian.config import ProviderConfig
from file_librarian.models import Classification, FileContext, Rules
from file_librarian.providers.base import ProviderError, Usage


import json

from pydantic import ValidationError

from file_librarian.providers.prompt import (
    normalize_answer,
    response_schema,
    system_prompt,
    user_message,
)


class ExampleProvider:
    name = "example"
    local = False  # True only if the model runs on this Mac (see below)
    attachments: frozenset[str] = frozenset()  # text only; see "Images and scanned PDFs"

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self.last_usage = Usage()

    def cost_usd(self, usage: Usage) -> float:
        # Your API's prices; price unknown models at your most expensive one.
        return (usage.input_tokens * 1.0 + usage.output_tokens * 5.0) / 1_000_000

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        # Send system_prompt(rules) and user_message(context), asking for
        # structured output with response_schema(rules).
        raw = ...
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            return Classification.model_validate(normalize_answer(data))
        except (ValueError, ValidationError) as exc:
            raise ProviderError(f"invalid answer: {exc}") from exc
```

Rules for every provider:

- **Return data only.** Never read, move or write files, and never return paths.
- **Validate with `Classification`.** Its schema rejects extra keys and
  out-of-range values. The classifier also rejects categories not in `rules`.
- **Raise the right error.** The classifier owns retries, rate limits, the
  cost cap and the `failed` status; it decides from what you raise:
  - `ProviderError("...", retryable=True)` for transient trouble (rate limit,
    overload, timeout, connection): retried with exponential backoff;
  - `ProviderError("...")` when this file cannot be classified (bad request,
    refusal, invalid answer): the file is marked failed and retried on later
    runs up to `max_attempts`;
  - `FatalProviderError("...")` when nothing can work (bad credentials,
    unknown model): the run stops before anything moves.
- **Implement the whole `Provider` protocol** (`providers/base.py`); mypy
  checks it. Besides `classify`:
  - set `self.last_usage = Usage(input_tokens, output_tokens, [models])`
    after every call, failed ones included, so `limits.max_cost_usd_per_run`
    holds;
  - `cost_usd(usage)` turns that into dollars, with your provider's own price
    table kept in your module. If your backend reports the cost itself (the
    `claude` CLI does), put it in `Usage(cost_usd=...)` and return it;
  - `local = True` only when the model runs on this Mac. It skips the
    requests-per-minute spacing and nothing else: `never_send` and sensitive
    files are withheld from local providers too. Decide it from where the
    requests go, not from the provider's name (`ollama` pointed at another
    machine is not local; `openai-compat` on `localhost` is);
  - `attachments`: the media types your model can read (below).
- **Send only what is in `context` and `rules`.** It is already capped at
  `max_chars_per_file`, and sensitive files never reach you.
- **Send `rules.examples` along.** They are the user's recent corrections
  (RF-29), newest first and already capped by `provider.max_examples`: the
  file's kind, extension and description, and the category or name before and
  after. Present them as data, like the file text (see `example_entry` in
  `providers/prompt.py`, the prompt and schema both Claude providers share).
- **Repeatability comes from the schema, not from sampling settings.** Use the
  API's structured output mode with `response_schema(rules)` from
  `providers/prompt.py` (not `Classification.model_json_schema()`: that one
  has optional fields and no category `enum`, which strict structured-output
  modes reject or ignore), and validate every answer after
  `normalize_answer`. If a server may not support the schema mode, fall back
  to a plain JSON mode with the schema in the instructions, as
  `openai_compat.py` does, and validate just as strictly. Send sampling parameters (`temperature`, `top_p`,
  `top_k`) only where the API documents them as supported: current Claude
  models reject non-default values with HTTP 400, so the `anthropic` provider
  sends none and relies on structured outputs (`output_config.format`).
- **Read secrets through `credentials.resolve`**, never from the config file:
  it looks in the environment, the Keychain and `provider.env_file`, in the
  order `provider.api_key_source` says. Give your provider its own
  environment variable and Keychain account (`KeySpec`), so its key never
  replaces another provider's or is sent to the wrong server.
- **Over HTTP, send only to the configured URL.** Refuse redirects instead of
  following them, reject credentials in the URL, never log or echo the key,
  and keep loopback traffic away from proxies (see `build_opener` in
  `providers/openai_compat.py`). Prefer the standard library to a new
  dependency.
- **Disable your SDK's own retries** (for example `max_retries=0`) so the
  classifier's backoff and rate limit are the only ones.
- **Calling a CLI instead of an HTTP API** (like `claude-code`): pass the
  file through stdin, never on the command line; give the process an
  allow-listed environment, not yours, so no unrelated secret leaks; run it in
  an empty temporary folder; turn off its own tools and retries; and find the
  executable without relying on PATH, which is minimal in a scheduled run
  (see `find_claude` in `providers/claude_code.py`).
- Treat file text as untrusted data: put it in a clearly delimited block of the
  prompt and tell the model it is content to describe, not instructions.

### Images and scanned PDFs (vision)

With `vision.enabled` (off by default), images and PDFs with no text layer can
be sent to the model. Declare what your provider reads in `attachments`:

| media type        | what you get in `context.attachment`                          |
|-------------------|---------------------------------------------------------------|
| `image/jpeg`      | the image as a JPEG, downscaled to `vision.max_image_px`, metadata (EXIF, GPS) stripped |
| `application/pdf` | a PDF of the first `vision.max_pdf_pages` pages (`attachment.pages`) |

The classifier attaches one only when its media type is in `attachments`,
so a provider that leaves the set empty (or lists only `image/jpeg`) never
sees one it cannot read: those files are classified from their metadata,
exactly as with vision off. Your provider does not prepare anything; it only
sends `attachment.data` the way its API takes images or documents (the
`anthropic` provider sends base64 `image` and `document` content blocks
before the text). Treat what the image shows as untrusted, like file text,
and make sure its tokens are part of the usage you report, so the cost cap
holds.

## 2. Register it

In `providers/__init__.py`, add a factory to `REGISTRY` (and remove the name
from `PLANNED` if it was there). The factory takes the `ProviderConfig` and
imports your module lazily, so an SDK is loaded only when its provider is used:

```python
def _example(config: ProviderConfig) -> Provider:
    from file_librarian.providers.example import ExampleProvider

    return ExampleProvider(config)


REGISTRY = {..., "example": _example}
```

Add the name to `PROVIDER_NAMES` in `config.py` too.

## 3. Test it offline

Tests must not use the network (the suite blocks connections and DNS).
Inject a fake HTTP client or a recorded response; for a provider that speaks
HTTP itself, the `fake_openai` fixture runs an in-process server
(`tests/fake_openai.py`) on 127.0.0.1, and its `loopback_network` fixture
allows connections to that address only, in that test only. For a CLI, use a
fake executable like `tests/fake_claude.py`, installed by a fixture in
`tests/conftest.py`. Assert that:

- a valid answer becomes the expected `Classification`;
- malformed JSON, an extra key or an out-of-range confidence raise `ProviderError`;
- the request contains no more than `max_chars_per_file` characters of file text;
- if `attachments` is not empty, a context with an `attachment` puts it in the
  request (see `tests/unit/test_vision.py`).

- authentication errors are `FatalProviderError`, rate limits, server
  errors and timeouts are retryable, and the usage and cost add up.

See `tests/unit/test_providers.py` and `tests/unit/test_openai_compat_provider.py`
for the shape.

## 4. Document it

Add the provider to the table in `providers/__init__.py`, to the README (its
own section, the Privacy section, and the vision paragraph if it reads
images), to the `provider.name` comment and its settings in
`config.example.yaml`, and to the CHANGELOG.
