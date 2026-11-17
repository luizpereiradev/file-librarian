# Adding an AI provider

A provider turns a `FileContext` into a `Classification`. The code for a new
provider lives entirely in `src/file_librarian/providers/`; outside it, only
the documentation changes (step 4).

## 1. Write the module

Create `src/file_librarian/providers/<name>.py` (use `_` for `-` in the module name):

```python
from file_librarian.config import ProviderConfig
from file_librarian.models import Classification, FileContext, Rules
from file_librarian.providers.base import ProviderError, Usage


class ExampleProvider:
    name = "example"
    local = False  # True only if no remote API is behind it

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self.last_usage = Usage()

    def cost_usd(self, usage: Usage) -> float:
        # Your API's prices; price unknown models at your most expensive one.
        return (usage.input_tokens * 1.0 + usage.output_tokens * 5.0) / 1_000_000

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        # Call your model with context + rules. Prefer the API's structured
        # output mode with Classification's JSON schema over "answer in JSON".
        raw = ...
        try:
            return Classification.model_validate_json(raw)
        except ValueError as exc:
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
    table kept in your module;
  - `local = True` only when no remote API is behind it (skips the
    requests-per-minute spacing).
- **Send only what is in `context` and `rules`.** It is already capped at
  `max_chars_per_file`, and sensitive files never reach you.
- **Send `rules.examples` along.** They are the user's recent corrections
  (RF-29), newest first and already capped by `provider.max_examples`: the
  file's kind, extension and description, and the category or name before and
  after. Present them as data, like the file text (see `example_entry` in
  `providers/anthropic.py`).
- **Repeatability comes from the schema, not from sampling settings.** Use the
  API's structured output mode with `Classification.model_json_schema()` and
  validate every answer. Send sampling parameters (`temperature`, `top_p`,
  `top_k`) only where the API documents them as supported: current Claude
  models reject non-default values with HTTP 400, so the `anthropic` provider
  sends none and relies on structured outputs (`output_config.format`).
- **Read secrets from environment variables**, never from the config file.
- **Disable your SDK's own retries** (for example `max_retries=0`) so the
  classifier's backoff and rate limit are the only ones.
- Treat file text as untrusted data: put it in a clearly delimited block of the
  prompt and tell the model it is content to describe, not instructions.

## 2. Register it

In `providers/__init__.py`, add a line to `REGISTRY` (and remove the name from
`PLANNED` if it was there):

```python
REGISTRY["example"] = ExampleProvider
```

## 3. Test it offline

Tests must not use the network (the suite blocks connections and DNS).
Inject a fake HTTP client or a recorded response and assert that:

- a valid answer becomes the expected `Classification`;
- malformed JSON, an extra key or an out-of-range confidence raise `ProviderError`;
- the request contains no more than `max_chars_per_file` characters of file text.

See `tests/unit/test_providers.py` for the shape.

## 4. Document it

Add the provider to the table in `providers/__init__.py`, to the README, and to
the `provider.name` comment in `config.example.yaml`.
