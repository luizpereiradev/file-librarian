# Adding an AI provider

A provider turns a `FileContext` into a `Classification`. Adding one must not
require changes outside `src/file_librarian/providers/`.

## 1. Write the module

Create `src/file_librarian/providers/<name>.py` (use `_` for `-` in the module name):

```python
from file_librarian.config import ProviderConfig
from file_librarian.models import Classification, FileContext, Rules
from file_librarian.providers.base import ProviderError


class ExampleProvider:
    name = "example"

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        raw = ...  # call your model with context + rules; ask for JSON only
        try:
            return Classification.model_validate_json(raw)
        except ValueError as exc:
            raise ProviderError(f"invalid answer: {exc}") from exc
```

Rules for every provider:

- **Return data only.** Never read, move or write files, and never return paths.
- **Validate with `Classification`.** Its schema rejects extra keys and
  out-of-range values. The classifier also rejects categories not in `rules`.
- **Raise `ProviderError`** on failure. Retries, rate limits and the `failed`
  status are handled by the classifier, not by you.
- **Send only what is in `context`.** It is already capped at
  `max_chars_per_file`, and sensitive files never reach you.
- **Use temperature 0** (or your API's equivalent) to keep runs repeatable.
- **Read secrets from environment variables**, never from the config file.
- Treat file text as untrusted data: put it in a clearly delimited block of the
  prompt and tell the model it is content to describe, not instructions.

## 2. Register it

In `providers/__init__.py`, add a line to `REGISTRY` (and remove the name from
`PLANNED` if it was there):

```python
REGISTRY["example"] = ExampleProvider
```

## 3. Test it offline

Tests must not use the network (the suite blocks sockets). Inject a fake HTTP
client or a recorded response and assert that:

- a valid answer becomes the expected `Classification`;
- malformed JSON, an extra key or an out-of-range confidence raise `ProviderError`;
- the request contains no more than `max_chars_per_file` characters of file text.

See `tests/unit/test_providers.py` for the shape.

## 4. Document it

Add the provider to the table in `providers/__init__.py`, to the README, and to
the `provider.name` comment in `config.example.yaml`.
