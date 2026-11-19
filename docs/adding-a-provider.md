# Adding an AI provider

A provider turns a `FileContext` into a `Classification`. It only decides:
it never reads, moves or names files. The reference implementations, all in
`src/file_librarian/providers/`: `anthropic.py` (an SDK), `claude_code.py` (a
CLI), `openai_compat.py` (plain HTTP with the standard library) and
`ollama.py` (a preset: a subclass that changes defaults and messages).

## What you will touch

| File | What | When |
| --- | --- | --- |
| `providers/<name>.py` | the provider (step 1) | always |
| `providers/__init__.py` | a factory in `REGISTRY`, a row in the docstring table (step 2) | always |
| `config.py` | the name in `PROVIDER_NAMES`; new settings as fields of `ProviderConfig` (step 3) | always |
| `credentials.py`, `keychain.py`, `cli.py` | its key (step 4) | it takes an API key |
| `tests/conftest.py` | its key's variable in `fake_home` (step 4) | it takes an API key |
| `cli.py` | `flib check-config` reports; warnings about settings it ignores (step 3) | it has settings or something to check |
| `onboarding.py`, `cli.py` | `flib init` offers it, with `DEFAULT_MODELS` | optional |
| `tests/unit/test_<name>_provider.py`, `tests/unit/test_providers.py` | tests (step 5) | always |
| README, `config.example.yaml`, CHANGELOG | docs (step 6) | always |

## 1. Write the module

Create `src/file_librarian/providers/<name>.py` (`_` for `-` in the module
name). Take the transport (SDK client, HTTP opener, executable path) as an
optional constructor argument, so tests can inject a fake:

```python
"""Example: classification through the Example API."""

from __future__ import annotations

import json
from collections.abc import Callable
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

# (system prompt, user message, JSON schema) -> (answer text, input tokens, output tokens)
Send = Callable[[str, str, dict[str, Any]], tuple[str, int, int]]


class ExampleProvider:
    name = "example"
    local = False  # True only if the model runs on this Mac (see below)
    attachments: frozenset[str] = frozenset()  # text only; see "Images and scanned PDFs"

    def __init__(self, config: ProviderConfig, send: Send | None = None) -> None:
        self._config = config
        self._send = send or self._post  # tests pass a fake
        self.last_usage = Usage()

    def cost_usd(self, usage: Usage) -> float:
        # Your API's prices; price unknown models at your most expensive one.
        return (usage.input_tokens * 1.0 + usage.output_tokens * 5.0) / 1_000_000

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        self.last_usage = Usage()
        raw, tokens_in, tokens_out = self._send(
            system_prompt(rules), user_message(context), response_schema(rules)
        )
        self.last_usage = Usage(tokens_in, tokens_out, [self._config.model])
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            return Classification.model_validate(normalize_answer(data))
        except (ValueError, ValidationError) as exc:
            raise ProviderError(f"invalid answer: {exc}") from exc

    def _post(self, system: str, user: str, schema: dict[str, Any]) -> tuple[str, int, int]:
        # Call the API here and map its failures (see "Raise the right error"):
        #   401/403 -> FatalProviderError; 429, 5xx, timeouts -> retryable ProviderError.
        raise FatalProviderError("not implemented")
```

`system_prompt`, `user_message`, `response_schema` and `normalize_answer`
(`providers/prompt.py`) are what every provider sends and how every answer is
cleaned up; reuse them, so a change to the prompt reaches all providers and
the golden tests in `tests/unit/test_prompt_payload.py`.

Rules for every provider:

- **Return data only.** Never read, move or write files, and never return paths.
- **Validate with `Classification`** after `normalize_answer`. The schema
  rejects extra keys and missing fields; `normalize_answer` clamps confidence
  into 0-1 and trims long strings, so small slips do not fail a file. The
  classifier also rejects categories not in `rules`.
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
  checks it through the factory's return type. Besides `classify`:
  - set `self.last_usage = Usage(input_tokens, output_tokens, [models])`
    after every call, failed ones included, so `limits.max_cost_usd_per_run`
    holds;
  - `cost_usd(usage)` turns that into dollars, with your provider's own price
    table kept in your module. If your backend reports the cost itself (the
    `claude` CLI does), put it in `Usage(cost_usd=...)` and return it. If
    your provider cannot know its prices (a server whose prices the user did
    not configure), add a `prices_known` attribute that is false then: runs
    record their cost as unknown instead of 0;
  - `local = True` only when the model runs on this Mac. It skips the
    requests-per-minute spacing and nothing else: `never_send` and sensitive
    files are withheld from local providers too. Decide it from where the
    requests go, not from the provider's name (`ollama` pointed at another
    machine is not local; `openai-compat` on `localhost` is);
  - `attachments`: the media types your model can read (below).
- **Send only what is in `context` and `rules`.** `context.text` is already
  cut to `provider.max_chars_per_file` and sensitive files never reach you;
  do not re-read the file or add to it.
- **Send `rules.examples` along.** They are the user's recent corrections
  (RF-29); `system_prompt(rules)` already includes them as data.
- **Repeatability comes from the schema, not from sampling settings.** Use the
  API's structured output mode with `response_schema(rules)` (not
  `Classification.model_json_schema()`: that one has optional fields and no
  category `enum`, which strict structured-output modes reject or ignore). If
  a server may not support the schema mode, fall back to a plain JSON mode
  with the schema in the instructions, as `openai_compat.py` does, and
  validate just as strictly. Send sampling parameters (`temperature`, `top_p`,
  `top_k`) only where the API documents them as supported: current Claude
  models reject non-default values with HTTP 400.
- **Over HTTP, send only to the configured URL.** Refuse redirects instead of
  following them, reject credentials in the URL, never log or echo the key,
  and keep loopback traffic away from proxies (see `build_opener` in
  `openai_compat.py`). Prefer the standard library to a new dependency
  (CONTRIBUTING.md, "Dependencies").
- **Disable your SDK's own retries** (for example `max_retries=0`) so the
  classifier's backoff and rate limit are the only ones.
- **Calling a CLI instead of an HTTP API** (like `claude-code`): pass the
  file through stdin, never on the command line; give the process an
  allow-listed environment, not yours, so no unrelated secret leaks; run it in
  an empty temporary folder; turn off its own tools and retries; and find the
  executable without relying on PATH, which is minimal in a scheduled run
  (see `find_claude` in `claude_code.py`).

### Images and scanned PDFs (vision)

With `vision.enabled` (off by default), images and PDFs with no text layer can
be sent to the model. Declare what your provider reads in `attachments`:

| media type        | what you get in `context.attachment`                          |
|-------------------|---------------------------------------------------------------|
| `image/jpeg`      | the image as a JPEG, downscaled to `vision.max_image_px`, metadata (EXIF, GPS) stripped |
| `application/pdf` | a PDF of the first `vision.max_pdf_pages` pages (`attachment.pages`) |

The classifier attaches one only when its media type is in `attachments`, so
a provider that leaves the set empty never sees one it cannot read: those
files are classified from their metadata, exactly as with vision off. Send
`attachment.data` the way your API takes images or documents (the `anthropic`
provider sends base64 `image` and `document` blocks before the text);
`user_message(context)` already says an attachment follows. Make sure the image tokens are in the usage you report, so the cost cap holds.

## 2. Register it

In `providers/__init__.py`, add a factory to `REGISTRY` and a row to the
table in the module docstring. The factory imports your module lazily, so an
SDK is loaded only when its provider is used:

```python
def _example(config: ProviderConfig) -> Provider:
    from file_librarian.providers.example import ExampleProvider

    return ExampleProvider(config)


REGISTRY = {..., "example": _example}
```

## 3. Configure it

- Add the name to `PROVIDER_NAMES` in `config.py`
  (`test_config_and_registry_agree_on_provider_names` checks both lists).
- A new setting is a typed field of `ProviderConfig` with a default and a
  docstring naming the providers that use it. If your provider reuses a
  setting of another one (`base_url`, `price_input_per_mtok`...), look at
  `COMPAT_PROVIDERS` and `COMPAT_ONLY_OPTIONS` in `cli.py`: as they are,
  `flib check-config` warns that those settings apply to `openai-compat` and
  `ollama` only.
- Anything `flib check-config` should report (where content goes, whether
  the backend answers) goes in a `_report_<name>` function in `cli.py`, like
  `_report_compat`. It must not send file content.
- Add the settings, commented, to `config.example.yaml`; a test loads it.

## 4. Credentials (if it takes an API key)

- **Never read the key from the config file.** Call
  `credentials.resolve(config)`: it looks in the environment, the Keychain
  and `provider.env_file`, in the order `provider.api_key_source` says.
- Give it its **own** environment variable and Keychain account, so its key
  never replaces another provider's or is sent to the wrong server: an
  account constant in `keychain.py`, a `KeySpec` in `credentials.spec_for`,
  and the spec in `credentials.all_specs` (which `flib doctor` uses to redact
  every key).
- `flib key --provider <name>` works through `spec_for`; update the
  provider names in its help and error texts in `cli.py`.
- Add the variable to the list `fake_home` deletes in `tests/conftest.py`, so
  a key in your shell never reaches the test suite.

## 5. Test it offline

Tests never use the network, the real Keychain or your files: the suite
blocks connections and DNS, points `HOME` at a temporary folder and replaces
the Keychain with a fake (CONTRIBUTING.md, "Writing tests"). Choose the fake
by transport:

- **Your own `send` or an SDK client:** pass a fake to the constructor, as
  `tests/unit/test_anthropic_provider.py` does with `FakeClient` and `reply`
  from `tests/_helpers.py`.
- **HTTP with the standard library:** the `fake_openai` fixture runs an
  in-process server (`tests/fake_openai.py`) on 127.0.0.1, and its
  `loopback_network` fixture allows connections to that address only, in
  that test only (see `test_openai_compat_provider.py`).
- **A CLI:** a fake executable like `tests/fake_claude.py`, installed by a
  fixture in `tests/conftest.py`.
- **Keys:** `monkeypatch.setenv` for the environment; `keychain.write(key,
  account)` writes to the fake Keychain.

Cover, in `tests/unit/test_<name>_provider.py`:

- `get_provider(ProviderConfig(name="<name>", model="m"))` returns your
  provider and `isinstance(provider, Provider)` holds (add it to
  `test_registry` in `tests/unit/test_providers.py` too);
- a valid answer becomes the expected `Classification`, and the request
  carries `system_prompt`, `user_message` and the schema;
- malformed JSON, a JSON array, an extra key and a non-numeric confidence
  raise `ProviderError`;
- authentication errors are `FatalProviderError`; rate limits, server errors
  and timeouts are `ProviderError` with `retryable=True`;
- `last_usage` and `cost_usd` add up, failed calls included;
- if `attachments` is not empty, a context with an `attachment` puts it in
  the request (see `tests/unit/test_vision.py`);
- with a key: it is read from each source and sent only where it should be;
- one run end to end: `cfg = fake_config(downloads, provider={"name":
  "<name>", ...})`, a file `put` in `downloads` (it makes the file old
  enough to be stable), then `pipeline.run(cfg, YourProvider(cfg.provider,
  fake), simulate=False)`, and the file is in its category folder under
  `library(downloads)` (renamed, if your fake is confident enough);
- `flib check-config` on a config using it (`write_config` and typer's
  `CliRunner`) exits 0 without warnings.

## 6. Document it

- **README.md:** the Requirements list, its own section (configuration,
  where the key comes from, trade-offs), the Privacy section (what goes
  where), and the vision paragraph if it reads images.
- **`config.example.yaml`:** the `provider.name` comment and its settings.
- **CHANGELOG.md:** one entry at the top of `### Added` under
  `## [Unreleased]`.

## Checklist for the pull request

- [ ] `providers/<name>.py` with a docstring; `REGISTRY` and its table; `PROVIDER_NAMES`
- [ ] transport injectable; errors mapped to `ProviderError` / retryable / `FatalProviderError`
- [ ] `last_usage` set on every call; `cost_usd`; `local` and `attachments` decided
- [ ] key (if any): own variable and Keychain account, `spec_for`, `all_specs`, `fake_home`
- [ ] tests from step 5, none touching the network, Keychain or real folders
- [ ] README, `config.example.yaml`, CHANGELOG
- [ ] the four checks in CONTRIBUTING.md pass, coverage at or above 93%
