"""The whole run, stage by stage: scan, context, classify, plan, execute.

``run`` is what ``flib run`` and ``flib simulate`` call, and what the tests
drive with a fake provider. A simulation stops after planning: it moves
nothing and records no run or file state, but it does keep the
classifications it paid for in the cache, so the ``flib run`` that follows
reuses them. A real run holds the instance lock from the first read of the
index to the last write.

With ``search.semantic.enabled``, a real run ends by embedding the rows that
are new or changed (``embeddings.refresh``). That step never fails the run: a
problem is reported in ``Outcome.embedding`` and retried by the next run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

from file_librarian.classifier import CACHE_DAYS, classify_all, correction_examples
from file_librarian.config import Config
from file_librarian.context import build_context_safely
from file_librarian.db.store import Store
from file_librarian.embeddings import RefreshReport, refresh
from file_librarian.executor import RunReport, execute, recover
from file_librarian.lock import instance_lock
from file_librarian.models import CategoryRule, FileStatus, Rules, RunTrigger
from file_librarian.planner import Plan, plan
from file_librarian.providers.base import Provider
from file_librarian.scanner import ScanResult, scan
from file_librarian.status import DUPLICATES_MOVED


@dataclass(slots=True)
class Outcome:
    scan: ScanResult
    plan: Plan
    report: RunReport | None
    """``None`` for a simulation."""
    recovered: int = 0
    cost_usd: float = 0.0
    """Estimated provider cost; leaves out calls at unknown prices (see ``cost_known``)."""
    deferred: int = 0
    """Left for the next run: over ``max_files_per_run`` or the cost cap."""
    cached: int = 0
    """Classifications reused from an earlier simulate or run, at no cost."""
    cost_known: bool = True
    """False when the provider's prices are unknown; the run then records no cost."""
    embedding: RefreshReport | None = None
    """Semantic search's vectors made after the run; ``None`` when it is off or simulated."""


log = logging.getLogger(__name__)


def rules_for(config: Config, store: Store | None = None) -> Rules:
    """The rules for the provider; with a store, past corrections become examples."""
    examples = correction_examples(store.current_corrections(), config) if store is not None else ()
    return Rules(
        categories=tuple(CategoryRule(c.name, c.hint) for c in config.categories),
        language=config.language,
        examples=examples,
        date_order=config.naming.date_order,
    )


def run(
    config: Config,
    provider: Provider,
    *,
    simulate: bool,
    trigger: RunTrigger = RunTrigger.MANUAL,
    now: float | None = None,
) -> Outcome:
    if simulate:
        with Store.open(config.database) as store:
            return _run(config, provider, store, simulate=True, trigger=trigger, now=now)
    with instance_lock(config.database), Store.open(config.database) as store:
        return _run(config, provider, store, simulate=False, trigger=trigger, now=now)


def _run(
    config: Config,
    provider: Provider,
    store: Store,
    *,
    simulate: bool,
    trigger: RunTrigger,
    now: float | None,
) -> Outcome:
    recovered = 0 if simulate else recover(store, config)
    store.prune_cache(max_age_days=CACHE_DAYS)
    found = scan(config, store.file_state, now=now)
    contexts = [build_context_safely(c, config) for c in found.candidates]
    classified = classify_all(contexts, provider, rules_for(config, store), config, cache=store)
    # Duplicates are never classified: no provider call, no cost (RF-21).
    planned = plan(
        classified.decisions,
        config,
        today=date.fromtimestamp(now) if now is not None else None,
        duplicates=found.duplicates,
        made_by_flib=store.flib_folders(),
    )
    deferred = found.deferred + len(classified.deferred)
    if simulate:
        return Outcome(
            found,
            planned,
            None,
            cost_usd=classified.cost_usd,
            deferred=deferred,
            cached=classified.cached,
            cost_known=classified.cost_known,
        )
    report = execute(
        planned,
        config,
        store,
        trigger=trigger,
        provider=provider.name,
        model=config.provider.model,
        seen=len(found.candidates) + len(found.duplicates),
        deferred=deferred,
        cost_usd=classified.run_cost,
    )
    if report.count(FileStatus.DUPLICATE, moved=True) and store.meta(DUPLICATES_MOVED) is None:
        # Remembered so the one-time note about duplicates is shown (``status.py``).
        store.set_meta(DUPLICATES_MOVED, datetime.now(UTC).isoformat(timespec="seconds"))
    return Outcome(
        found,
        planned,
        report,
        recovered,
        classified.cost_usd,
        deferred,
        classified.cached,
        classified.cost_known,
        embed_after_run(config, store),
    )


def embed_after_run(config: Config, store: Store) -> RefreshReport | None:
    """Vectors for new and changed rows; any failure is reported, never raised."""
    if not config.search.semantic.enabled:
        return None
    try:
        result = refresh(store, config)
    except Exception as exc:  # the files are filed already; never fail the run for this
        result = RefreshReport(error=f"{type(exc).__name__}: {exc}"[:300])
    if result.error:
        log.warning("semantic search: embedding failed, retried next run: %s", result.error)
    return result
