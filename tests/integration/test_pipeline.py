"""End to end: scan, context, classify (fake), plan, execute and undo, on real temp folders."""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from file_librarian import pipeline
from file_librarian.config import Config, parse_config
from file_librarian.db.store import Store
from file_librarian.executor import execute, file_record, recover
from file_librarian.lock import AlreadyRunningError, instance_lock
from file_librarian.models import Classification, FileStatus, OperationStatus
from file_librarian.planner import Decision, plan
from file_librarian.providers.fake import FakeProvider
from file_librarian.undo import NothingToUndoError, undo_file, undo_run
from tests._helpers import make_docx, make_pdf

pytestmark = pytest.mark.integration

OLD = time.time() - 3600


def answer(
    category: str, name: str = "thing", confidence: float = 0.95, **kw: Any
) -> Classification:
    return Classification(
        category=category,
        suggested_name=name,
        description=f"A {name}.",
        tags=["t"],
        confidence=confidence,
        **kw,
    )


def make_config(downloads: Path, **overrides: Any) -> Config:
    raw: dict[str, Any] = {
        "inputs": [str(downloads)],
        "provider": {"name": "fake", "model": "fixed"},
    }
    return parse_config(raw | overrides)


def put(folder: Path, name: str, content: bytes = b"data") -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (OLD, OLD))
    return path


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "Library" not in p.parts
    }


def rows(config: Config, sql: str) -> list[sqlite3.Row]:
    with Store.open(config.database) as store:
        return list(store.conn.execute(sql))


@pytest.fixture
def cfg(downloads: Path) -> Config:
    return make_config(downloads)


def test_run_organizes_renames_and_indexes(downloads: Path, cfg: Config) -> None:
    put(downloads, "documento(3).pdf", b"%PDF invoice")
    provider = FakeProvider(
        {
            "documento(3).pdf": answer(
                "Finance", "Electricity invoice", document_date=date(2026, 10, 5)
            )
        }
    )
    outcome = pipeline.run(cfg, provider, simulate=False)

    target = downloads / "Organized" / "Finance" / "2026-10-05_electricity-invoice.pdf"
    assert target.read_bytes() == b"%PDF invoice"
    assert not (downloads / "documento(3).pdf").exists()
    assert outcome.report is not None and outcome.report.moved == 1
    [row] = rows(cfg, "SELECT * FROM files")
    assert row["status"] == "organized"
    assert row["original_name"] == "documento(3).pdf"
    assert row["current_path"] == str(target)
    assert row["category"] == "Finance"
    assert row["description"] == "A Electricity invoice."
    [op] = rows(cfg, "SELECT * FROM operations")
    assert op["status"] == "done"


def test_low_confidence_and_unknown_category_go_to_review_unrenamed(
    downloads: Path, cfg: Config
) -> None:
    put(downloads, "unsure.pdf", b"a")
    put(downloads, "weird.pdf", b"b")
    provider = FakeProvider(
        {
            "unsure.pdf": answer("Finance", "something", confidence=0.4),
            "weird.pdf": answer("Taxes", "something"),
        }
    )
    pipeline.run(cfg, provider, simulate=False)
    review = downloads / "Organized" / "_Review"
    assert sorted(p.name for p in review.iterdir()) == ["unsure.pdf", "weird.pdf"]
    statuses = {
        r["original_name"]: (r["status"], r["category"]) for r in rows(cfg, "SELECT * FROM files")
    }
    assert statuses == {"unsure.pdf": ("review", None), "weird.pdf": ("review", None)}


def test_rename_needs_its_own_confidence(downloads: Path, cfg: Config) -> None:
    put(downloads, "scan01.pdf")
    pipeline.run(cfg, FakeProvider({"scan01.pdf": answer("Finance", "bill", 0.75)}), simulate=False)
    assert (downloads / "Organized" / "Finance" / "scan01.pdf").exists()


def test_category_rename_policy_and_units_keep_names(downloads: Path, cfg: Config) -> None:
    put(downloads, "Setup-2.1.dmg")
    put(downloads, "project/main.py")
    os.utime(downloads / "project", (OLD, OLD))
    provider = FakeProvider(
        {
            "Setup-2.1.dmg": answer("Installers", "some installer"),
            "project": answer("Other", "python project"),
        }
    )
    pipeline.run(cfg, provider, simulate=False)
    assert (downloads / "Organized" / "Installers" / "Setup-2.1.dmg").exists()
    assert (downloads / "Organized" / "Other" / "project" / "main.py").exists()


def test_sensitive_file_stays_and_is_never_sent(downloads: Path, cfg: Config) -> None:
    secret = put(downloads, "notes.txt", ("token = " + "ghp_" + "a" * 36).encode())
    provider = FakeProvider(default=answer("Other"))
    pipeline.run(cfg, provider, simulate=False)
    assert secret.exists()
    assert provider.calls == []
    [row] = rows(cfg, "SELECT * FROM files")
    assert row["status"] == "sensitive"
    assert row["description"] is None
    pipeline.run(cfg, provider, simulate=False)  # known: not processed again
    assert provider.calls == []


def test_provider_failure_is_retried_then_given_up(downloads: Path, cfg: Config) -> None:
    path = put(downloads, "flaky.pdf")
    provider = FakeProvider()  # no answer: every call fails
    for _ in range(3):
        pipeline.run(cfg, provider, simulate=False)
    assert path.exists()
    [row] = rows(cfg, "SELECT * FROM files")
    assert (row["status"], row["attempts"]) == ("failed", 3)
    assert len(provider.calls) == 3
    pipeline.run(cfg, provider, simulate=False)
    assert len(provider.calls) == 3  # max_attempts reached: not sent again


def test_second_run_changes_nothing(downloads: Path, cfg: Config) -> None:
    put(downloads, "a.pdf")
    provider = FakeProvider(default=answer("Finance", "a"))
    pipeline.run(cfg, provider, simulate=False)
    before = snapshot(downloads)
    outcome = pipeline.run(cfg, provider, simulate=False)
    assert outcome.plan.items == []
    assert snapshot(downloads) == before


def test_collisions_get_a_counter_and_never_overwrite(downloads: Path, cfg: Config) -> None:
    existing = put(downloads, "Organized/Finance/2026-10-05_bill.pdf", b"precious")
    put(downloads, "x.pdf", b"one")
    put(downloads, "y.pdf", b"two")
    same = answer("Finance", "bill", document_date=date(2026, 10, 5))
    pipeline.run(cfg, FakeProvider(default=same), simulate=False)
    folder = downloads / "Organized" / "Finance"
    assert existing.read_bytes() == b"precious"
    assert sorted(p.name for p in folder.iterdir()) == [
        "2026-10-05_bill-2.pdf",
        "2026-10-05_bill-3.pdf",
        "2026-10-05_bill.pdf",
    ]


def test_hostile_model_answers_stay_inside_output_root(downloads: Path, cfg: Config) -> None:
    put(downloads, "a.pdf", b"a")
    put(downloads, "b.pdf", b"b")
    put(downloads, "c.pdf", b"c")
    provider = FakeProvider(
        {
            "a.pdf": answer("Finance", "../../../../etc/passwd"),
            "b.pdf": answer("../../..", "x"),
            "c.pdf": answer("Finance", "/" * 10),
        }
    )
    pipeline.run(cfg, provider, simulate=False)
    output = downloads / "Organized"
    moved = {p.relative_to(output).as_posix() for p in output.rglob("*.pdf")}
    today = date.today().isoformat()
    assert moved == {
        f"Finance/{today}_etc-passwd.pdf",  # path pieces flattened into one safe name
        "_Review/b.pdf",  # unknown category
        "Finance/c.pdf",  # unusable name: original kept
    }
    assert not any(p for p in downloads.parent.rglob("*.pdf") if not p.is_relative_to(output))


def test_simulate_changes_nothing(downloads: Path, cfg: Config) -> None:
    put(downloads, "a.pdf")
    before = snapshot(downloads)
    outcome = pipeline.run(cfg, FakeProvider(default=answer("Finance", "a")), simulate=True)
    assert len(outcome.plan.moves()) == 1
    assert outcome.report is None
    assert snapshot(downloads) == before
    assert rows(cfg, "SELECT * FROM files") == []


def test_file_changed_after_scan_is_left_alone(downloads: Path, cfg: Config) -> None:
    path = put(downloads, "a.pdf", b"v1")
    outcome = pipeline.run(cfg, FakeProvider(default=answer("Finance", "a")), simulate=True)
    path.write_bytes(b"v2 edited")
    with Store.open(cfg.database) as store:
        report = execute(
            outcome.plan, cfg, store, trigger="manual", provider="fake", model="m", seen=1
        )
    assert path.read_bytes() == b"v2 edited"
    assert report.items[0].status is FileStatus.SKIPPED


def test_undo_last_run_restores_everything(downloads: Path, cfg: Config) -> None:
    put(downloads, "a.pdf", b"A")
    make_docx(downloads / "b.docx", ["contract"])
    os.utime(downloads / "b.docx", (OLD, OLD))
    put(downloads, "proj/x.txt")
    os.utime(downloads / "proj", (OLD, OLD))
    before = snapshot(downloads)
    pipeline.run(cfg, FakeProvider(default=answer("Finance", "doc")), simulate=False)
    assert snapshot(downloads) != before

    with Store.open(cfg.database) as store:
        report = undo_run(store)
    assert len(report.restored) == 3 and report.skipped == []
    after = {k: v for k, v in snapshot(downloads).items() if not k.startswith("Organized")}
    assert after == before
    statuses = {r["status"] for r in rows(cfg, "SELECT status FROM files")}
    assert statuses == {"skipped"}
    assert {r["status"] for r in rows(cfg, "SELECT status FROM operations")} == {"undone"}
    # Restored files are left alone by the next run.
    outcome = pipeline.run(cfg, FakeProvider(default=answer("Finance", "doc")), simulate=False)
    assert outcome.plan.items == []


def test_undo_skips_changed_and_blocked_files(downloads: Path, cfg: Config) -> None:
    put(downloads, "edit-me.txt", b"original")
    put(downloads, "blocked.txt", b"mine")
    pipeline.run(cfg, FakeProvider(default=answer("Other", "x", 0.75)), simulate=False)
    other = downloads / "Organized" / "Other"
    (other / "edit-me.txt").write_bytes(b"edited after the move")
    (downloads / "blocked.txt").write_bytes(b"a new file with the old name")

    with Store.open(cfg.database) as store:
        report = undo_run(store)
    reasons = {path.name: reason for path, reason in report.skipped}
    assert reasons == {
        "edit-me.txt": "changed since it was moved",
        "blocked.txt": f"original path is taken: {downloads / 'blocked.txt'}",
    }
    assert (other / "edit-me.txt").read_bytes() == b"edited after the move"
    assert (downloads / "blocked.txt").read_bytes() == b"a new file with the old name"


def test_undo_single_file_and_nothing_to_undo(downloads: Path, cfg: Config) -> None:
    with Store.open(cfg.database) as store, pytest.raises(NothingToUndoError):
        undo_run(store)
    put(downloads, "a.pdf")
    put(downloads, "b.pdf", b"bb")
    pipeline.run(cfg, FakeProvider(default=answer("Finance", "z", 0.75)), simulate=False)
    with Store.open(cfg.database) as store:
        report = undo_file(store, downloads / "Organized" / "Finance" / "a.pdf")
    assert [src.name for _, src in report.restored] == ["a.pdf"]
    assert (downloads / "a.pdf").exists()
    assert (downloads / "Organized" / "Finance" / "b.pdf").exists()


def test_recover_resolves_interrupted_moves(downloads: Path, cfg: Config) -> None:
    done_src = put(downloads, "moved.pdf")
    stuck_src = put(downloads, "stuck.pdf", b"s")
    found = pipeline.run(cfg, FakeProvider(default=answer("Finance", "x", 0.75)), simulate=True)
    decisions = [Decision(i.context, i.classification) for i in found.plan.items]
    planned = plan(decisions, cfg)
    with Store.open(cfg.database) as store:
        run_id = store.start_run("run", "manual")
        for item in planned.items:
            assert item.destination is not None
            record = file_record(item, FileStatus.ORGANIZED, "fake", "m")
            file_id = store.upsert_file(record, run_id=run_id)
            store.add_operation(run_id, file_id, item.context.path, item.destination)
        # Simulate a crash after the first rename only.
        first = planned.items[0]
        assert first.destination is not None
        first.destination.parent.mkdir(parents=True)
        done_src.rename(first.destination)
        assert recover(store) == 2
        ops = {Path(o["src_path"]).name: o["status"] for o in store.operations()}
    assert ops == {
        "moved.pdf": OperationStatus.DONE.value,
        "stuck.pdf": OperationStatus.FAILED.value,
    }
    assert stuck_src.exists()
    statuses = {r["original_name"]: r["status"] for r in rows(cfg, "SELECT * FROM files")}
    assert statuses == {"moved.pdf": "organized", "stuck.pdf": "failed"}


def test_runs_are_exclusive(downloads: Path, cfg: Config) -> None:
    with instance_lock(cfg.database), pytest.raises(AlreadyRunningError):
        pipeline.run(cfg, FakeProvider(), simulate=False)


def test_offline_fake_provider_works_end_to_end(downloads: Path) -> None:
    config = make_config(downloads, language="pt-BR")
    make_pdf(downloads / "fatura.pdf", "Fatura de energia")
    os.utime(downloads / "fatura.pdf", (OLD, OLD))
    pipeline.run(config, FakeProvider(echo=True), simulate=False)
    moved = list((downloads / "Organizado" / "Outros").iterdir())
    assert len(moved) == 1 and moved[0].suffix == ".pdf"
