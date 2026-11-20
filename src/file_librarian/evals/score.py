"""Scoring an evaluation run, saving it, and comparing two saved runs.

A run is a list of ``ItemResult`` (what flib decided for each corpus item,
next to its gold labels) plus what it cost. ``metrics`` turns it into the
numbers ``flib eval`` reports; ``to_json`` and ``load`` save and read them
back; ``compare`` puts two saved runs side by side.

How each number is counted:

- **Category accuracy**: over items with at least one acceptable category,
  the share where the model's category (the folder it chose, or the one it
  suggested for an item it sent to review) is acceptable. An item that was
  not classified (failed, or deferred by the cost cap) counts as wrong.
  Per category, items are grouped under their first acceptable category.
- **Confusion pairs**: (expected, chosen) for each wrong answer, most common first.
- **Review**: how many items went to review, how many the gold expects there,
  and how many of those did.
- **Name kept**: over items filed (not in review) whose gold says whether the
  name should be kept, with "kept" as the positive class: precision is the
  share of kept names that should have been kept, recall the share of names
  to keep that were. A name counts as kept when the file was not renamed or
  the model chose its original subject (``name_choice: original``).
- **Sensitive**: items flib must withhold, how many it withheld, and how many
  reached the provider. That last number must be 0; ``flib eval`` fails otherwise.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

FORMAT_VERSION = 1
NOT_CLASSIFIED = "(none)"


@dataclass(frozen=True, slots=True)
class ItemResult:
    path: str
    status: str
    """``organized``, ``review``, ``sensitive``, ``failed``, or ``deferred`` (cost cap)."""
    expected: tuple[str, ...]
    category: str | None = None
    """The model's category; ``None`` when it gave none."""
    confidence: float | None = None
    expected_review: bool = False
    expected_kept: bool | None = None
    kept: bool | None = None
    """Whether the original name survived; ``None`` unless the item was filed."""
    name: str | None = None
    """The name it would get; ``None`` when it stays where it is."""
    sensitive: bool = False
    """Gold: flib must withhold it."""
    sent: bool = False
    """It reached the provider."""

    @property
    def correct(self) -> bool | None:
        """``None`` when no category is right (the item is scored on review only)."""
        if not self.expected:
            return None
        return self.category in self.expected

    @property
    def reviewed(self) -> bool:
        return self.status == "review"


@dataclass(slots=True)
class Usage:
    elapsed_seconds: float = 0.0
    calls: int = 0
    """Provider calls, retries included."""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cost_known: bool = True


@dataclass(slots=True)
class Run:
    meta: dict[str, Any]
    items: list[ItemResult]
    usage: Usage = field(default_factory=Usage)

    @property
    def leaked(self) -> list[str]:
        """Sensitive items that reached the provider. Must be empty."""
        return [i.path for i in self.items if i.sensitive and i.sent]


def _ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def metrics(run: Run) -> dict[str, Any]:
    items = run.items
    scored = [i for i in items if i.correct is not None]
    per_category: dict[str, dict[str, Any]] = {}
    for item in scored:
        entry = per_category.setdefault(item.expected[0], {"items": 0, "correct": 0})
        entry["items"] += 1
        entry["correct"] += bool(item.correct)
    for entry in per_category.values():
        entry["accuracy"] = _ratio(entry["correct"], entry["items"])
    confusion = Counter(
        (item.expected[0], item.category or NOT_CLASSIFIED) for item in scored if not item.correct
    )
    candidates = [i for i in items if not i.sensitive]
    reviewed = [i for i in candidates if i.reviewed]
    expected_review = [i for i in candidates if i.expected_review]
    named = [i for i in items if i.status == "organized" and i.expected_kept is not None]
    kept = [i for i in named if i.kept]
    should_keep = [i for i in named if i.expected_kept]
    both = [i for i in kept if i.expected_kept]
    sensitive = [i for i in items if i.sensitive]
    minutes = run.usage.elapsed_seconds / 60
    sent = sum(1 for i in items if i.sent)
    return {
        "items": len(items),
        "category": {
            "scored": len(scored),
            "correct": sum(1 for i in scored if i.correct),
            "accuracy": _ratio(sum(1 for i in scored if i.correct), len(scored)),
            "per_category": dict(sorted(per_category.items())),
            "confusion": [
                {"expected": e, "chosen": c, "count": n}
                for (e, c), n in sorted(confusion.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
        },
        "review": {
            "reviewed": len(reviewed),
            "rate": _ratio(len(reviewed), len(candidates)),
            "expected": len(expected_review),
            "expected_and_reviewed": sum(1 for i in expected_review if i.reviewed),
        },
        "name_kept": {
            "scored": len(named),
            "precision": _ratio(len(both), len(kept)),
            "recall": _ratio(len(both), len(should_keep)),
        },
        "sensitive": {
            "expected": len(sensitive),
            "withheld": sum(1 for i in sensitive if i.status == "sensitive" and not i.sent),
            "sent": len(run.leaked),
        },
        "status": dict(sorted(Counter(i.status for i in items).items())),
        "sent": sent,
        "items_per_minute": round(sent / minutes, 1) if minutes > 0 else None,
        "input_tokens": run.usage.input_tokens,
        "output_tokens": run.usage.output_tokens,
        "cost_usd": round(run.usage.cost_usd, 6) if run.usage.cost_known else None,
    }


def to_json(run: Run) -> dict[str, Any]:
    return {
        "format": FORMAT_VERSION,
        "meta": run.meta,
        "metrics": metrics(run),
        "usage": asdict(run.usage),
        "items": [asdict(i) for i in run.items],
    }


class ResultError(ValueError):
    """A saved result cannot be read."""


def load(path: Path) -> Run:
    """Read a run saved with ``flib eval --out``."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format") != FORMAT_VERSION:
            raise ResultError(f"{path}: not a flib eval result (format {data.get('format')!r})")
        items = [
            ItemResult(**(raw | {"expected": tuple(raw["expected"])})) for raw in data["items"]
        ]
        return Run(dict(data["meta"]), items, Usage(**data["usage"]))
    except ResultError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ResultError(f"{path}: cannot read the result: {exc}") from exc


# --- Reports -----------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _label(run: Run) -> str:
    m = run.meta
    return f"{m.get('provider')}/{m.get('model')} {m.get('language')} prompt v{m.get('prompt')}"


def report_lines(run: Run, *, worst: int = 8) -> list[str]:
    """The human-readable summary ``flib eval`` prints."""
    m = metrics(run)
    cat, review, names, sens = m["category"], m["review"], m["name_kept"], m["sensitive"]
    lines = [
        f"Evaluated {m['items']} items with {_label(run)} "
        f"(corpus v{run.meta.get('corpus')}, {run.meta.get('date')}).",
        f"Category accuracy: {_pct(cat['accuracy'])} ({cat['correct']}/{cat['scored']})",
    ]
    ranked = sorted(cat["per_category"].items(), key=lambda kv: (kv[1]["accuracy"] or 0, kv[0]))
    lines += [
        f"  {name:<22} {_pct(entry['accuracy']):>7}  ({entry['correct']}/{entry['items']})"
        for name, entry in ranked
    ]
    if cat["confusion"]:
        lines.append("Most common mistakes (expected -> chosen):")
        lines += [
            f"  {c['expected']} -> {c['chosen']}: {c['count']}" for c in cat["confusion"][:worst]
        ]
    lines += [
        f"Review: {review['reviewed']} item(s) sent to review ({_pct(review['rate'])}); "
        f"{review['expected']} expected there, {review['expected_and_reviewed']} of them went.",
        f"Name kept: precision {_pct(names['precision'])}, recall {_pct(names['recall'])} "
        f"over {names['scored']} filed item(s).",
        f"Sensitive: {sens['withheld']}/{sens['expected']} withheld, {sens['sent']} sent.",
        "Status: " + ", ".join(f"{k} {v}" for k, v in m["status"].items()) + ".",
    ]
    speed = m["items_per_minute"]
    cost = m["cost_usd"]
    lines.append(
        f"{m['sent']} sent in {run.usage.elapsed_seconds:.1f} s"
        + (f" ({speed} items/min)" if speed is not None else "")
        + f"; {m['input_tokens']} input and {m['output_tokens']} output tokens; cost "
        + ("unknown" if cost is None else f"US$ {cost:.4f}")
        + "."
    )
    return lines


def compare(base: Run, other: Run, *, changed: int = 20) -> list[str]:
    """Two runs side by side: the main numbers, then the items whose verdict changed."""
    a, b = metrics(base), metrics(other)

    def row(name: str, x: float | None, y: float | None, pct: bool = True) -> str:
        show = _pct if pct else (lambda v: "n/a" if v is None else f"{v:.4g}")
        delta = ""
        if x is not None and y is not None:
            diff = (y - x) * (100 if pct else 1)
            delta = f"{diff:+.1f} pts" if pct else f"{diff:+.4g}"
        return f"  {name:<24} {show(x):>10} {show(y):>10} {delta:>10}"

    lines = [f"A: {_label(base)}", f"B: {_label(other)}"]
    if base.meta.get("corpus") != other.meta.get("corpus"):
        lines.append("warning: the two runs scored different corpus versions")
    if base.meta.get("language") != other.meta.get("language"):
        lines.append("warning: the two runs used different languages, so different categories")
    if base.meta.get("limit") != other.meta.get("limit"):
        lines.append("warning: the two runs scored different samples (--limit)")
    lines.append(f"  {'':<24} {'A':>10} {'B':>10} {'B - A':>10}")
    lines += [
        row("category accuracy", a["category"]["accuracy"], b["category"]["accuracy"]),
        row("review rate", a["review"]["rate"], b["review"]["rate"]),
        row(
            "expected reviews caught",
            _ratio(a["review"]["expected_and_reviewed"], a["review"]["expected"]),
            _ratio(b["review"]["expected_and_reviewed"], b["review"]["expected"]),
        ),
        row("name kept precision", a["name_kept"]["precision"], b["name_kept"]["precision"]),
        row("name kept recall", a["name_kept"]["recall"], b["name_kept"]["recall"]),
        row("sensitive sent", a["sensitive"]["sent"], b["sensitive"]["sent"], pct=False),
        row("items/min", a["items_per_minute"], b["items_per_minute"], pct=False),
        row("cost US$", a["cost_usd"], b["cost_usd"], pct=False),
    ]
    categories = sorted(set(a["category"]["per_category"]) | set(b["category"]["per_category"]))
    for name in categories:
        x = a["category"]["per_category"].get(name, {}).get("accuracy")
        y = b["category"]["per_category"].get(name, {}).get("accuracy")
        if x != y:
            lines.append(row(f"  {name}"[:24], x, y))
    before = {i.path: i for i in base.items}
    flips = [
        (item, before[item.path])
        for item in other.items
        if item.path in before
        and item.correct is not None
        and item.correct != before[item.path].correct
    ]
    if flips:
        lines.append(f"Items whose category changed verdict ({len(flips)}):")
        for item, old in flips[:changed]:
            mark = "fixed" if item.correct else "broke"
            lines.append(
                f"  {mark:<6} {item.path}: {old.category or NOT_CLASSIFIED} -> "
                f"{item.category or NOT_CLASSIFIED}"
            )
        if len(flips) > changed:
            lines.append(f"  ... and {len(flips) - changed} more")
    return lines
