"""The whole run, stage by stage: scan, context, classify, plan, execute.

``run`` is what ``flib run`` and ``flib simulate`` call, and what the tests
drive with a fake provider. A simulation stops after planning: it moves
nothing and records no run or file state, but it does keep the
classifications it paid for in the cache, so the ``flib run`` that follows
reuses them. A real run holds the instance lock from the first read of the
index to the last write.

An automatic run (the daily run, or watch mode) also keeps to
``limits.max_cost_usd_per_day``: what automatic runs cost today, local time,
counts against it (``runs.cost_usd``), and the run's own cap is lowered to
what is left. Once nothing is left, it sends no file to the provider; files of
inputs with a fixed category, cached answers and duplicates still go ahead.
With a provider whose cost is unknown (``ProviderConfig.cost_unknown``) the
cost cannot stop anything, so ``limits.max_files_per_day`` limits how many
files today's automatic runs may see instead. If the provider stops the run
(``FatalProviderError``) after calls were billed, a run row still records
that cost, so the cap counts it.

A watch run (#90) takes only items quiet for ``schedule.watch_quiet_seconds``
(``Config.for_watch``) and, when more are ready than ``max_files_per_run``,
new arrivals before items an earlier watch run left behind.

An automatic run waits up to ``lock_wait`` seconds for the instance lock. Once
it holds it, the menu bar item is told a run started (``menubar_state``).

With ``search.semantic.enabled``, a real run ends by embedding the rows that
are new or changed (``embeddings.refresh``). That step never fails the run: a
problem is reported in ``Outcome.embedding`` and retried by the next run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from file_librarian import menubar_state
from file_librarian.classifier import (
    CACHE_DAYS,
    classify_all,
    correction_examples,
    rule_decisions,
)
from file_librarian.config import Config
from file_librarian.context import build_context_safely
from file_librarian.db.store import Store
from file_librarian.embeddings import RefreshReport, refresh
from file_librarian.executor import RECHECK_HASH_MAX_BYTES, RunReport, execute, recover
from file_librarian.lock import instance_lock
from file_librarian.models import CategoryRule, FileStatus, Rules, RunMode, RunTrigger
from file_librarian.planner import Plan, plan
from file_librarian.providers.base import FatalProviderError, Provider
from file_librarian.scanner import (
    SETTLING_KEY,
    Candidate,
    ScanMemory,
    ScanResult,
    Settling,
    scan,
)
from file_librarian.scheduler import watch as watch_mod
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
    day_cap_reached: bool = False
    """An automatic run that left files for later because of ``limits.max_cost_usd_per_day``."""


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
    lock_wait: float = 0.0,
    memory: ScanMemory | None = None,
) -> Outcome:
    """``memory``: digests and future-dated items a watch process already knows."""
    if trigger is RunTrigger.WATCH:
        config = config.for_watch()
    if simulate:
        with Store.open(config.database) as store:
            return _run(
                config, provider, store, simulate=True, trigger=trigger, now=now, memory=memory
            )
    with instance_lock(config.database, wait=lock_wait):
        # The menu bar item turns busy; it is told again when the lock is released.
        menubar_state.changed(config.database)
        with Store.open(config.database) as store:
            return _run(
                config, provider, store, simulate=False, trigger=trigger, now=now, memory=memory
            )


def _run(
    config: Config,
    provider: Provider,
    store: Store,
    *,
    simulate: bool,
    trigger: RunTrigger,
    now: float | None,
    memory: ScanMemory | None = None,
) -> Outcome:
    recovered = 0 if simulate else recover(store, config)
    store.prune_cache(max_age_days=CACHE_DAYS)
    config, files_left = _files_capped(config, store, trigger, simulate=simulate)
    hashes = memory.hashes if memory is not None else None
    settling = memory.settling if memory is not None else Settling.loads(store.meta(SETTLING_KEY))
    if hashes is not None:
        # The executor re-hashes only small files before moving: never trust the cache
        # for the larger ones.
        hashes.verify_above = RECHECK_HASH_MAX_BYTES
    try:
        found = scan(
            config,
            store.file_state,
            now=now,
            prefer=_prefer(store, trigger),
            hashes=hashes,
            settling=settling,
        )
    finally:
        if hashes is not None:
            hashes.verify_above = None
    if not simulate:
        store.set_meta(SETTLING_KEY, settling.dumps())
    contexts = [build_context_safely(c, config) for c in found.candidates]
    # Files of inputs with a fixed category are never sent: no provider call, no cost (#89).
    ruled, contexts = rule_decisions(contexts, config)
    capped, left_today = _day_capped(config, store, trigger, simulate=simulate)
    try:
        classified = classify_all(contexts, provider, rules_for(config, store), capped, cache=store)
    except FatalProviderError as exc:
        # What was billed counts toward the daily cap; with unknown prices (``None``) the
        # files seen count toward limits.max_files_per_day.
        spent = getattr(exc, "spent", 0.0)
        if not simulate and (spent is None or spent > 0):
            run_id = store.start_run(RunMode.RUN, trigger)
            store.finish_run(
                run_id, seen=len(found.candidates), moved=0, review=0, errors=0, cost_usd=spent
            )
        raise
    day_cap_reached = left_today is not None and (left_today <= 0 or bool(classified.deferred))
    if files_left is not None and (files_left <= 0 or found.deferred):
        day_cap_reached = True
    # Duplicates are never classified: no provider call, no cost (RF-21).
    planned = plan(
        [*ruled, *classified.decisions],
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
    if hashes is not None:
        for item in report.items:
            if item.status is FileStatus.SKIPPED and item.note.startswith("changed since"):
                hashes.forget(item.source)  # the re-hash disagreed: never reuse that digest
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
        day_cap_reached,
    )


def _prefer(store: Store, trigger: RunTrigger) -> Callable[[Candidate], bool] | None:
    """For a watch run: new arrivals first, so a backlog never keeps them out."""
    if trigger is not RunTrigger.WATCH:
        return None
    handled = watch_mod.handled_keys(store)
    return lambda candidate: watch_mod.item_key(candidate) not in handled


def _files_capped(
    config: Config, store: Store, trigger: RunTrigger, *, simulate: bool
) -> tuple[Config, int | None]:
    """With a provider whose cost is unknown, an automatic run sees at most what is left
    of ``limits.max_files_per_day``; that number, or ``None`` when it does not bind."""
    cap = config.limits.files_cap_per_day
    if simulate or cap is None or not trigger.automatic or not config.provider.cost_unknown:
        return config, None
    left = cap - store.automatic_files_today()
    if left >= config.limits.max_files_per_run:
        return config, None
    limits = config.limits.model_copy(update={"max_files_per_run": max(left, 0)})
    return config.model_copy(update={"limits": limits}), left


def _day_capped(
    config: Config, store: Store, trigger: RunTrigger, *, simulate: bool
) -> tuple[Config, float | None]:
    """The configuration to classify with, and what the daily cap leaves today when that
    is less than the run's own cap (``None`` when the daily cap does not bind)."""
    cap = config.limits.cost_cap_per_day
    if simulate or cap is None or not trigger.automatic:
        return config, None
    left = cap - store.automatic_cost_today()
    per_run = config.limits.max_cost_usd_per_run
    if per_run is not None and per_run <= left:
        return config, None
    limits = config.limits.model_copy(update={"max_cost_usd_per_run": max(left, 0.0)})
    return config.model_copy(update={"limits": limits}), left


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
