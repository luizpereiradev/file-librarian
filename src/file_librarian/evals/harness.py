"""Run the evaluation corpus through the real pipeline, in isolation, and score it.

``flib eval`` never touches the user's files or settings: the corpus is
written to a new temporary folder, the configuration is built from the
command's options alone (the user's config file is never read), and the
database is a throwaway one inside that folder, so the user's library, index
and classification cache are never opened. The pipeline runs in simulate
mode: it classifies and plans, and moves nothing. The folder is deleted
afterwards.

The provider is wrapped in a ``Recorder`` that notes every context it is
given and adds up the tokens billed, so the report can prove that no
sensitive item was sent.

Text recognition is replayed, not run: the corpus's images with text come
with the text macOS recognised in them, recorded once (``evals/ocr.json``),
so a run scores the same on every Mac, in CI and wherever macOS's text
recognition is missing; every other file has no text, as a photo would.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from file_librarian import __version__, pipeline
from file_librarian.classifier import PROMPT_VERSION, prices_known, wants_attachment
from file_librarian.config import Config, ConfigError, parse_config
from file_librarian.context import build_context_safely
from file_librarian.db.store import Store
from file_librarian.evals import corpus
from file_librarian.evals.score import ItemResult, Run, Usage
from file_librarian.extractors import ocr
from file_librarian.locales import Language
from file_librarian.models import Classification, FileContext, FileStatus, Rules
from file_librarian.providers.base import Provider
from file_librarian.providers.base import Usage as CallUsage
from file_librarian.reprocess import estimated_usage
from file_librarian.scanner import scan

DEFAULT_MODELS = {
    "anthropic": "claude-haiku-5-5",
    "claude-code": "haiku",
    "fake": "offline",
}


@dataclass(frozen=True, slots=True)
class Settings:
    provider: str = "anthropic"
    model: str | None = None
    language: Language = "en"
    limit: int | None = None
    max_cost_usd: float = 1.0
    base_url: str | None = None
    seed: int = corpus.DEFAULT_SEED
    vision: bool = False
    """Send images and scanned PDFs to the model (``vision.enabled``)."""
    ocr: bool = True
    """Use the recorded text of the images with text (``ocr.enabled``)."""
    cases: tuple[str, ...] = ()
    """Only the items with one of these hard-case tags (``image-text``...)."""

    @property
    def model_name(self) -> str | None:
        return self.model or DEFAULT_MODELS.get(self.provider)


class SettingsError(ValueError):
    """The options do not describe a provider flib can evaluate."""


def eval_config(root: Path, settings: Settings, files: int) -> Config:
    """A configuration with every path inside ``root``: nothing real can be touched."""
    model = settings.model_name
    if model is None:
        raise SettingsError(f"--model is required for the {settings.provider} provider")
    provider: dict[str, Any] = {"name": settings.provider, "model": model}
    if settings.base_url is not None:
        provider["base_url"] = settings.base_url
    try:
        return parse_config(
            {
                "language": settings.language,
                "inputs": [str(root / "inbox")],
                "output_root": str(root / "library"),
                "database": str(root / "index" / "library.db"),
                "provider": provider,
                "limits": {
                    "stable_seconds": 0,
                    "max_files_per_run": max(files, 1),
                    "max_cost_usd_per_run": settings.max_cost_usd,
                },
                "search": {"semantic": {"enabled": False}},
                "vision": {"enabled": settings.vision},
                "ocr": {"enabled": settings.ocr},
            }
        )
    except ConfigError as exc:
        raise SettingsError(str(exc)) from exc


@dataclass(slots=True)
class Prepared:
    """The corpus written to a temporary folder, with the configuration to run it."""

    root: Path
    items: list[corpus.Item]
    config: Config
    settings: Settings


@contextmanager
def prepared(settings: Settings) -> Iterator[Prepared]:
    """Write the corpus into a new temporary folder; delete the folder afterwards."""
    chosen = corpus.select(corpus.items(settings.seed), settings.limit, settings.seed)
    if settings.cases:
        chosen = [i for i in chosen if set(i.gold.cases) & set(settings.cases)]
        if not chosen:
            raise SettingsError(f"no corpus item has the tag {', '.join(settings.cases)}")
    with TemporaryDirectory(prefix="flib-eval-") as tmp:
        root = Path(tmp).resolve()
        cfg = eval_config(root, settings, len(chosen))
        (root / "inbox").mkdir()
        corpus.build(root / "inbox", chosen)
        yield Prepared(root, chosen, cfg, settings)


@dataclass(frozen=True, slots=True)
class Estimate:
    to_send: int
    withheld: int
    cost_usd: float
    prices_known: bool


def estimate(prep: Prepared, provider: Provider) -> Estimate:
    """What sending the corpus would cost, from the length of each request."""
    rules = pipeline.rules_for(prep.config)
    with Store.open(prep.config.database) as store:
        found = scan(prep.config, store.file_state)
    with ocr.replay(corpus.recorded_ocr()):
        contexts = [build_context_safely(c, prep.config) for c in found.candidates]
    to_send = [c for c in contexts if not c.sensitive]
    cost = sum(
        provider.cost_usd(
            estimated_usage(c, rules, prep.config, wants_attachment(c, provider, prep.config))
        )
        for c in to_send
    )
    return Estimate(len(to_send), len(contexts) - len(to_send), cost, prices_known(provider))


class Recorder:
    """A provider that notes what it is sent and what it bills, then passes the call on."""

    def __init__(self, inner: Provider) -> None:
        self.inner = inner
        self.name = inner.name
        self.local = inner.local
        self.attachments = inner.attachments
        self.last_usage = CallUsage()
        self.sent: list[FileContext] = []
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    @property
    def prices_known(self) -> bool:
        return prices_known(self.inner)

    def cost_usd(self, usage: CallUsage) -> float:
        return self.inner.cost_usd(usage)

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        self.sent.append(context)
        self.calls += 1
        try:
            return self.inner.classify(context, rules)
        finally:
            usage = self.inner.last_usage
            self.inner.last_usage = CallUsage()
            self.last_usage = usage
            self.input_tokens += usage.input_tokens
            self.output_tokens += usage.output_tokens


def run(prep: Prepared, provider: Provider, *, clock: Callable[[], float] = time.monotonic) -> Run:
    """Simulate a run over the prepared corpus and score every item."""
    recorder = Recorder(provider)
    started = clock()
    with ocr.replay(corpus.recorded_ocr()):
        outcome = pipeline.run(prep.config, recorder, simulate=True)
    elapsed = clock() - started
    planned = {item.context.path.name: item for item in outcome.plan.items}
    sent = {ctx.path.name: sent_as(ctx) for ctx in recorder.sent}
    results = []
    lang = prep.settings.language
    for item in prep.items:
        gold = item.gold
        p = planned.get(item.path)
        answer = p.classification if p is not None else None
        status = p.status.value if p is not None else "deferred"
        kept = name = None
        if p is not None and p.destination is not None:
            name = p.destination.name
        if p is not None and p.status is FileStatus.ORGANIZED:
            kept = not p.renamed or p.name_choice == "original"
        results.append(
            ItemResult(
                path=item.path,
                status=status,
                expected=gold.categories[lang],
                category=answer.category if answer is not None else None,
                confidence=answer.confidence if answer is not None else None,
                expected_review=gold.review,
                expected_kept=gold.keep_name,
                kept=kept,
                name=name,
                sensitive=gold.sensitive,
                sent=item.path in sent,
                fields=answer_fields(answer),
                expected_fields=dict(gold.fields),
                cases=gold.cases,
                sent_as=sent.get(item.path),
            )
        )
    usage = Usage(
        elapsed_seconds=round(elapsed, 3),
        calls=recorder.calls,
        input_tokens=recorder.input_tokens,
        output_tokens=recorder.output_tokens,
        cost_usd=outcome.cost_usd,
        cost_known=outcome.cost_known,
    )
    return Run(meta(prep), results, usage)


def sent_as(ctx: FileContext) -> str:
    """What the provider got for ``ctx``: ``image+text``, ``image``, ``pdf``, ``text``
    or ``metadata``."""
    if ctx.attachment is not None:
        shown = "pdf" if ctx.attachment.media_type == "application/pdf" else "image"
        return f"{shown}+text" if ctx.text else shown
    return "text" if ctx.text else "metadata"


def answer_fields(answer: Classification | None) -> dict[str, str | None]:
    """The structured fields of an answer (#88) as ``score.FIELDS`` names them."""
    if answer is None:
        return {}
    return {
        "kind": answer.kind,
        "issuer": answer.issuer,
        "amount": answer.amount,
        "currency": answer.currency,
        "document_date": answer.document_date.isoformat() if answer.document_date else None,
        "due_date": answer.due_date.isoformat() if answer.due_date else None,
        "period": answer.period,
    }


def meta(prep: Prepared) -> dict[str, Any]:
    s = prep.settings
    return {
        "date": datetime.now(UTC).date().isoformat(),
        "flib": __version__,
        "provider": s.provider,
        "model": prep.config.provider.model,
        "language": s.language,
        "prompt": PROMPT_VERSION,
        "corpus": corpus.CORPUS_VERSION,
        "seed": s.seed,
        "limit": s.limit,
        "max_cost_usd": s.max_cost_usd,
        "vision": s.vision,
        "ocr": s.ocr,
        "cases": list(s.cases),
    }
