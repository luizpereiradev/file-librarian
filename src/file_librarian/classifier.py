"""Stage 3: classify contexts with a provider (RF-07, RF-08, RF-29, RF-30).

Wraps any ``Provider`` with what the provider itself should not decide:

- sensitive contexts are never sent;
- at most ``requests_per_minute`` calls, spaced evenly;
- retryable errors are retried up to ``max_retries`` times with exponential
  backoff; other errors fail only that file;
- a ``FatalProviderError`` stops the run before anything moves;
- answers are cached for ``CACHE_DAYS`` per content hash and rules, so a
  ``flib run`` right after ``flib simulate`` reuses them instead of paying again;
  the rules include the correction examples, so a new correction means fresh
  answers;
- the estimated cost of what was actually billed is tracked, and once it
  reaches ``limits.max_cost_usd_per_run`` the remaining files are deferred to
  the next run (not counted as failures).

The provider's answer is only data; the planner decides what it means.

``correction_examples`` picks the corrections sent along as examples: the
most recent ``provider.max_examples`` still in effect, never from a sensitive
file or one matching ``never_send``.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import ValidationError

from file_librarian.config import Config
from file_librarian.db.store import CorrectionRow
from file_librarian.models import Classification, Correction, Decision, FileContext, Rules
from file_librarian.providers.base import FatalProviderError, Provider, ProviderError, Usage
from file_librarian.sensitive import sensitive_name, withheld_by_config

CACHE_DAYS = 7
PROMPT_VERSION = "3"  # bump when the prompt or answer schema changes, to invalidate the cache
EXAMPLE_DESCRIPTION_CHARS = 200


class AnswerCache(Protocol):
    def cached_answer(self, sha256: str, rules_key: str, *, max_age_days: int) -> str | None: ...

    def cache_answer(self, sha256: str, rules_key: str, answer_json: str) -> None: ...


def rules_key(provider: Provider, rules: Rules, config: Config) -> str:
    """Identify everything that shapes an answer; any change means a fresh one."""
    p = config.provider
    material = json.dumps(
        {
            "v": PROMPT_VERSION,
            "provider": provider.name,
            "model": p.model,
            "effort": p.effort,
            "max_chars": p.max_chars_per_file,
            "language": rules.language,
            "date_order": rules.date_order,
            "categories": [[c.name, c.hint] for c in rules.categories],
            "examples": [
                [e.field, e.old_value, e.new_value, e.extension, e.is_unit, e.description]
                for e in rules.examples
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def correction_examples(rows: Iterable[CorrectionRow], config: Config) -> tuple[Correction, ...]:
    """The newest corrections to send as examples, at most ``provider.max_examples``.

    A file whose name is sensitive or matches ``never_send``, under its original
    or its current name, is never used: its content was withheld, and so is
    what was learned from it.
    """
    limit = config.provider.max_examples
    chosen: list[Correction] = []
    for row in rows:
        if len(chosen) >= limit:
            break
        names = {row.original_name, row.current_name}
        if row.field == "name" and row.old_value:
            names.add(row.old_value)
        if any(sensitive_name(n) or withheld_by_config(n, config.never_send) for n in names):
            continue
        description = (row.description or "")[:EXAMPLE_DESCRIPTION_CHARS] or None
        chosen.append(
            Correction(
                field="name" if row.field == "name" else "category",
                old_value=row.old_value,
                new_value=row.new_value,
                extension=row.extension,
                is_unit=row.is_unit,
                description=description,
            )
        )
    return tuple(chosen)


@dataclass(slots=True)
class Classified:
    decisions: list[Decision] = field(default_factory=list)
    deferred: list[FileContext] = field(default_factory=list)
    """Not sent because the cost cap was reached; retried next run."""
    cost_usd: float = 0.0
    cached: int = 0
    """Answers reused from the cache, at no cost."""


def classify_all(
    contexts: list[FileContext],
    provider: Provider,
    rules: Rules,
    config: Config,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    cache: AnswerCache | None = None,
) -> Classified:
    settings = config.provider
    cap = config.limits.max_cost_usd_per_run
    # Local providers (no API behind them) need no rate limit.
    interval = 0.0 if provider.local else 60.0 / settings.requests_per_minute
    result = Classified()
    last_call: float | None = None
    key = rules_key(provider, rules, config) if cache is not None else ""

    for ctx in contexts:
        if ctx.sensitive:
            result.decisions.append(Decision(ctx))
            continue
        if cache is not None:
            hit = cache.cached_answer(ctx.sha256, key, max_age_days=CACHE_DAYS)
            if hit is not None:
                try:
                    result.decisions.append(Decision(ctx, Classification.model_validate_json(hit)))
                    result.cached += 1
                    continue
                except ValidationError:
                    pass  # stale format: ask again
        if cap is not None and result.cost_usd >= cap:
            result.deferred.append(ctx)
            continue
        error = "not classified"
        for attempt in range(settings.max_retries + 1):
            if last_call is not None:
                wait = interval - (clock() - last_call)
                if wait > 0:
                    sleep(wait)
            last_call = clock()
            try:
                answer = provider.classify(ctx, rules)
            except FatalProviderError:
                raise
            except ProviderError as exc:
                result.cost_usd += _billed(provider)
                error = f"classification failed: {exc}"[:500]
                if exc.retryable and attempt < settings.max_retries:
                    sleep(min(2.0**attempt, 30.0))
                    continue
                result.decisions.append(Decision(ctx, error=error))
                break
            except ValidationError as exc:
                result.cost_usd += _billed(provider)
                result.decisions.append(Decision(ctx, error=f"invalid answer: {exc}"[:500]))
                break
            result.cost_usd += _billed(provider)
            result.decisions.append(Decision(ctx, answer))
            if cache is not None:
                cache.cache_answer(ctx.sha256, key, answer.model_dump_json())
            break
    return result


def _billed(provider: Provider) -> float:
    """Cost of the provider's last call; resets it so nothing is counted twice."""
    billed = provider.cost_usd(provider.last_usage)
    provider.last_usage = Usage()
    return billed
