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
  the next run (not counted as failures). A provider whose prices are not
  known (``prices_known`` false: an ``openai-compat`` server with no prices
  configured) makes the cost unknown rather than zero;
- a ``visual`` context (``vision.enabled``: an image or a scanned PDF) gets
  its image or first pages attached only when the provider lists that media
  type in ``attachments``, and only right before the call; otherwise, or when
  preparing it fails, it is classified from its text (an image's recognised
  text, #100) or its metadata, as with vision off. Answers that saw an
  attachment are cached under their own key, which also covers the vision
  limits, so turning vision on or off never reuses an answer given with the
  other setting. Answers given from text recognised by OCR, alone or with the
  image, are cached under a key that also covers the OCR settings that shape
  that text (languages, pages, the helper's version);
- under ``privacy.descriptions: redact`` (the default) every answer, from the
  provider or the cache, has its description, tags, proposed name and reason
  scrubbed (``privacy.scrub_answer``) before anything else sees it: this is the
  one place answers come through, so the cache, the index, full-text search,
  embeddings and the MCP tools only ever get the scrubbed text.

The provider's answer is only data; the planner decides what it means.

``rule_decisions`` sets apart the files of inputs with a fixed ``category``
(#89): they get that category with no provider call at all, so no cost, no
cache entry and no content sent anywhere. Their answer carries no
description; its tags are words of the file's name. A sensitive file among
them stays in place like any other.

``correction_examples`` picks the corrections sent along as examples: the
most recent ``provider.max_examples`` still in effect, never from a sensitive
file or one matching ``never_send``.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from pydantic import ValidationError

from file_librarian.config import Config, VisionConfig
from file_librarian.db.store import CorrectionRow
from file_librarian.extractors import ocr, visual
from file_librarian.models import (
    VISUAL_MEDIA_TYPES,
    Attachment,
    Classification,
    Correction,
    Decision,
    FileContext,
    Rules,
)
from file_librarian.privacy import scrubber
from file_librarian.providers.base import FatalProviderError, Provider, ProviderError, Usage
from file_librarian.sensitive import sensitive_name, withheld_by_config

CACHE_DAYS = 7
PROMPT_VERSION = "8"  # bump when the prompt or answer schema changes, to invalidate the cache
EXAMPLE_DESCRIPTION_CHARS = 200


class AnswerCache(Protocol):
    def cached_answer(self, sha256: str, rules_key: str, *, max_age_days: int) -> str | None: ...

    def cache_answer(self, sha256: str, rules_key: str, answer_json: str) -> None: ...


def rules_key(provider: Provider, rules: Rules, config: Config) -> str:
    """Identify everything that shapes an answer; any change means a fresh one."""
    p = config.provider
    shape: dict[str, object] = {
        "v": PROMPT_VERSION,
        "provider": provider.name,
        "model": p.model,
        "effort": p.effort,
        "max_chars": p.max_chars_per_file,
        "privacy": config.privacy.descriptions,
        "language": rules.language,
        "date_order": rules.date_order,
        "categories": [[c.name, c.hint] for c in rules.categories],
        "examples": [
            [e.field, e.old_value, e.new_value, e.extension, e.is_unit, e.description]
            for e in rules.examples
        ],
    }
    if p.base_url is not None:
        # Two servers can serve models with the same name; their answers differ.
        # Added only when set, so keys for providers without a URL don't change.
        shape["base_url"] = _server(p.base_url)
    material = json.dumps(
        shape,
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _server(url: str) -> str:
    """The URL with its scheme and host in lower case (they ignore case)."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))


def vision_key(text_key: str, config: Config) -> str:
    """The cache key for answers given with an attachment: the rules plus the vision limits."""
    v = config.vision
    material = f"{text_key}|vision|{v.max_image_px}|{v.max_pdf_pages}"
    return hashlib.sha256(material.encode()).hexdigest()


def ocr_key(text_key: str, config: Config) -> str:
    """The cache key for answers given from OCR text: the rules plus what shapes that text."""
    o = config.ocr
    material = json.dumps(
        [text_key, "ocr", ocr.HELPER_VERSION, o.languages, o.max_pages, ocr.MAX_PX],
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def answer_key(ctx: FileContext, text_key: str, config: Config, *, vision: bool) -> str:
    """The cache key ``ctx``'s answer is kept under: the rules, plus what shapes OCR text
    when it came from OCR, plus the vision limits when an attachment went along (an image
    sent with its recognised text depends on both)."""
    key = text_key
    if ctx.metadata.text_source == "ocr":
        key = ocr_key(key, config)
    if vision:
        key = vision_key(key, config)
    return key


def wants_attachment(ctx: FileContext, provider: Provider, config: Config) -> bool:
    """Vision is on, ``ctx`` is an image or scanned PDF, and the provider reads that type."""
    return (
        config.vision.enabled
        and ctx.visual is not None
        and VISUAL_MEDIA_TYPES[ctx.visual] in provider.attachments
    )


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
        if any(
            sensitive_name(n, config.sensitive.allow_names)
            or withheld_by_config(n, config.never_send)
            for n in names
        ):
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
    cost_known: bool = True
    """False once a call was billed at prices the provider does not know (``prices_known``):
    ``cost_usd`` then leaves those calls out."""
    cached: int = 0
    """Answers reused from the cache, at no cost."""

    @property
    def run_cost(self) -> float | None:
        """What the run records in ``runs.cost_usd``: ``None`` when the cost is unknown."""
        return self.cost_usd if self.cost_known else None


def classify_all(
    contexts: list[FileContext],
    provider: Provider,
    rules: Rules,
    config: Config,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    cache: AnswerCache | None = None,
    prepare: Callable[[FileContext, VisionConfig], Attachment] = visual.prepare,
) -> Classified:
    settings = config.provider
    cap = config.limits.max_cost_usd_per_run
    # Local providers (no API behind them) need no rate limit.
    interval = 0.0 if provider.local else 60.0 / settings.requests_per_minute
    result = Classified()
    last_call: float | None = None
    text_key = rules_key(provider, rules, config) if cache is not None else ""
    clean = scrubber(config.privacy.descriptions)

    for ctx in contexts:
        if ctx.sensitive:
            result.decisions.append(Decision(ctx))
            continue
        vision = wants_attachment(ctx, provider, config)
        key = answer_key(ctx, text_key, config, vision=vision) if cache is not None else ""
        if cache is not None:
            hit = cache.cached_answer(ctx.sha256, key, max_age_days=CACHE_DAYS)
            if hit is not None:
                try:
                    answer = clean(Classification.model_validate_json(hit))
                    result.decisions.append(Decision(replace(ctx, vision_used=vision), answer))
                    result.cached += 1
                    continue
                except ValidationError:
                    pass  # stale format: ask again
        if cap is not None and result.cost_usd >= cap:
            result.deferred.append(ctx)
            continue
        sent = ctx
        if vision:
            try:
                sent = replace(ctx, attachment=prepare(ctx, config.vision))
            except Exception:
                # Any trouble with the image or PDF: classify from metadata (RF-27).
                vision = False
                key = answer_key(ctx, text_key, config, vision=False) if cache is not None else ""
        error = "not classified"
        for attempt in range(settings.max_retries + 1):
            if last_call is not None:
                wait = interval - (clock() - last_call)
                if wait > 0:
                    sleep(wait)
            last_call = clock()
            try:
                answer = clean(provider.classify(sent, rules))
            except FatalProviderError:
                raise
            except ProviderError as exc:
                _bill(result, provider)
                error = f"classification failed: {exc}"[:500]
                if exc.retryable and attempt < settings.max_retries:
                    sleep(min(2.0**attempt, 30.0))
                    continue
                result.decisions.append(Decision(ctx, error=error))
                break
            except ValidationError as exc:
                _bill(result, provider)
                result.decisions.append(Decision(ctx, error=f"invalid answer: {exc}"[:500]))
                break
            _bill(result, provider)
            # The decision keeps no attachment: the bytes are needed for the call only.
            result.decisions.append(Decision(replace(ctx, vision_used=vision), answer))
            if cache is not None:
                cache.cache_answer(ctx.sha256, key, answer.model_dump_json())
            break
    return result


RULE_DESCRIPTION = "filed by its input's fixed category"
"""The answer's description for a file filed by rule; never stored (``by_rule``)."""
MAX_NAME_TAGS = 5
_WORD = re.compile(r"[^\W\d_]{3,}")


def name_tags(name: str) -> list[str]:
    """Up to ``MAX_NAME_TAGS`` words of a file name (letters only, three or more), lower case."""
    stem = name.rsplit(".", 1)[0] if "." in name.lstrip(".") else name
    tags: list[str] = []
    for word in _WORD.findall(stem):
        tag = word.casefold()[:40]
        if tag not in tags:
            tags.append(tag)
        if len(tags) >= MAX_NAME_TAGS:
            break
    return tags


def rule_decisions(
    contexts: Iterable[FileContext], config: Config
) -> tuple[list[Decision], list[FileContext]]:
    """The decisions for files of inputs with a fixed category, and the other contexts.

    No provider is involved: the category is the input's, the confidence 1, the
    name the file's own (``rename`` on the input applies the naming pattern to it).
    """
    decided: list[Decision] = []
    rest: list[FileContext] = []
    for ctx in contexts:
        rules = config.input_rules(ctx.input) if ctx.input is not None else None
        if rules is None or rules.category is None:
            rest.append(ctx)
            continue
        if ctx.sensitive:
            decided.append(Decision(ctx))  # stays where it is, like any sensitive file
            continue
        stem = ctx.path.stem if not ctx.is_unit else ctx.path.name
        answer = Classification(
            category=rules.category,
            suggested_name=(stem.strip() or "unnamed")[:200],
            description=RULE_DESCRIPTION,
            tags=name_tags(ctx.path.name),
            confidence=1.0,
            name_choice="original",
        )
        decided.append(Decision(ctx, answer, by_rule=True))
    return decided, rest


def _bill(result: Classified, provider: Provider) -> None:
    """Add the cost of the provider's last call; resets it so nothing is counted twice."""
    usage = provider.last_usage
    result.cost_usd += provider.cost_usd(usage)
    if usage.cost_usd is None and not prices_known(provider):
        result.cost_known = False
    provider.last_usage = Usage()


def prices_known(provider: Provider) -> bool:
    """Whether ``provider.cost_usd`` reflects real prices. A provider may say it does not
    (an optional ``prices_known`` attribute); by default it does."""
    return bool(getattr(provider, "prices_known", True))
