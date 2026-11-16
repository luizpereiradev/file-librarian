"""The whole run, stage by stage: scan, context, classify, plan, execute.

``run`` is what ``flib run`` and ``flib simulate`` call, and what the tests
drive with a fake provider. Simulation stops after planning and writes
nothing. A real run holds the instance lock from the first read of the index
to the last write.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from file_librarian.config import Config
from file_librarian.context import build_context
from file_librarian.db.store import Store
from file_librarian.executor import RunReport, execute, recover
from file_librarian.lock import instance_lock
from file_librarian.models import CategoryRule, FileContext, Rules
from file_librarian.planner import Decision, Plan, plan
from file_librarian.providers.base import Provider, ProviderError
from file_librarian.scanner import ScanResult, scan


@dataclass(slots=True)
class Outcome:
    scan: ScanResult
    plan: Plan
    report: RunReport | None
    """``None`` for a simulation."""
    recovered: int = 0


def rules_for(config: Config) -> Rules:
    return Rules(
        categories=tuple(CategoryRule(c.name, c.hint) for c in config.categories),
        language=config.language,
    )


def classify(contexts: list[FileContext], provider: Provider, rules: Rules) -> list[Decision]:
    decisions = []
    for ctx in contexts:
        if ctx.sensitive:
            decisions.append(Decision(ctx))
            continue
        try:
            decisions.append(Decision(ctx, provider.classify(ctx, rules)))
        except (ProviderError, ValidationError) as exc:
            decisions.append(Decision(ctx, error=f"classification failed: {exc}"[:500]))
    return decisions


def run(config: Config, provider: Provider, *, simulate: bool, trigger: str = "manual",
        now: float | None = None) -> Outcome:  # fmt: skip
    if simulate:
        with Store.open(config.database) as store:
            return _run(config, provider, store, simulate=True, trigger=trigger, now=now)
    with instance_lock(config.database), Store.open(config.database) as store:
        return _run(config, provider, store, simulate=False, trigger=trigger, now=now)


def _run(config: Config, provider: Provider, store: Store, *, simulate: bool, trigger: str,
         now: float | None) -> Outcome:  # fmt: skip
    recovered = 0 if simulate else recover(store)
    found = scan(config, store.file_state, now=now)
    contexts = [build_context(c, config) for c in found.candidates]
    decisions = classify(contexts, provider, rules_for(config))
    planned = plan(decisions, config)
    if simulate:
        return Outcome(found, planned, None)
    report = execute(
        planned,
        config,
        store,
        trigger=trigger,
        provider=provider.name,
        model=config.provider.model,
        seen=len(found.candidates),
        deferred=found.deferred,
    )
    return Outcome(found, planned, report, recovered)
