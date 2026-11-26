"""What the interface's JSON API returns (``/api/v1/...``).

Each payload is built from the same modules as the command line, and holds
counts only: never a file's name, path or contents. Text the model wrote (a
suggested category that is not configured) is masked, as in ``flib doctor``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from file_librarian import status as status_mod
from file_librarian.config import Config
from file_librarian.models import explain_review

API_VERSION = 1


def status(config: Config, *, now: datetime | None = None) -> dict[str, Any]:
    """``flib status`` as counts; raises ``status.StatusError`` or ``sqlite3.Error``."""
    now = now or datetime.now(UTC)
    state = status_mod.collect(config, now=now)
    configured = {c.name for c in config.categories}
    oldest = state.oldest_review
    run = state.last_run
    schedule = state.schedule
    return {
        "api": API_VERSION,
        "database_exists": state.database_exists,
        "review": {
            "total": state.review_total,
            "oldest_days": None if oldest is None else (now - oldest).days,
            "groups": [
                {
                    "reason": None if g.reason is None else g.reason.value,
                    "count": g.count,
                    "explanation": explain_review(
                        g.reason,
                        g.suggested
                        if g.suggested in configured or g.suggested is None
                        else "<other>",
                        None,
                    ),
                }
                for g in state.review
            ],
        },
        "failed": {
            "total": state.failed_total,
            "groups": [
                {"error": g.error, "count": g.count, "retried": g.retried} for g in state.failed
            ],
        },
        "sensitive": state.sensitive,
        "duplicates": state.duplicates,
        "due": None
        if state.due is None
        else {
            "within_days": state.due.within_days,
            "upcoming": state.due.upcoming,
            "overdue": state.due.overdue,
        },
        "last_run": None
        if run is None
        else {
            "trigger": run.trigger.value,
            "finished_at": run.finished_at or run.started_at,
            "organized": run.files_moved or 0,
            "review": run.files_review or 0,
            "duplicates": run.files_duplicates or 0,
            "failed": run.errors or 0,
            "cost_usd": run.cost_usd,
        },
        "schedule": {
            "installed": schedule.installed,
            "loaded": schedule.loaded,
            "mode": schedule.mode,
            "next_run": None if schedule.at is None else schedule.at.isoformat(),
        },
        "today": {
            "automatic_cost_usd": state.automatic_cost_today,
            "cap_usd": config.limits.cost_cap_per_day,
            "cap_reached": state.day_cap_reached,
        },
        "month": {
            "cost_usd": state.month_cost_usd,
            "runs_without_cost": state.month_unpriced_runs,
        },
    }
