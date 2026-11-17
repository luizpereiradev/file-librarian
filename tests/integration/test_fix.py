"""flib fix: correcting one file, and learning from it (RF-29)."""

from __future__ import annotations

import os
import plistlib
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from file_librarian import pipeline
from file_librarian.cli import app
from file_librarian.config import Config, parse_config
from file_librarian.db.store import FileRow, Store
from file_librarian.executor import recover
from file_librarian.finder_tags import USER_TAGS, read_tags
from file_librarian.fix import FixError, FixReport, fix_file
from file_librarian.fsops import move_no_replace
from file_librarian.models import Classification, Correction, FileStatus, OperationStatus
from file_librarian.providers.fake import FakeProvider
from file_librarian.search import search
from file_librarian.undo import undo_file, undo_run
from tests._helpers import OLD, case_insensitive, names, write_xattr

pytestmark = pytest.mark.integration


def answer(category: str, confidence: float = 0.75) -> Classification:
    return Classification(
        category=category,
        suggested_name="x",
        description=f"A {category.lower()} document.",
        confidence=confidence,
    )


def make_cfg(downloads: Path, **extra: Any) -> Config:
    provider = {"name": "fake", "model": "x"} | extra.pop("provider", {})
    return parse_config({"inputs": [str(downloads)], "provider": provider} | extra)


@pytest.fixture
def cfg(downloads: Path) -> Config:
    return make_cfg(downloads)


def drop(downloads: Path, files: dict[str, bytes]) -> None:
    for name, content in files.items():
        (downloads / name).write_bytes(content)
        os.utime(downloads / name, (OLD, OLD))


def organize(downloads: Path, cfg: Config, files: dict[str, tuple[bytes, Classification]]) -> None:
    drop(downloads, {name: content for name, (content, _) in files.items()})
    provider = FakeProvider({name: a for name, (_, a) in files.items()})
    pipeline.run(cfg, provider, simulate=False)


def lib(downloads: Path) -> Path:
    return downloads / "Organized"


def row_for(cfg: Config, original: str) -> FileRow:
    with Store.open(cfg.database) as store:
        return next(r for r in store.all_files() if r.original_name == original)


def fix(cfg: Config, path: Path, **kwargs: Any) -> FixReport:
    with Store.open(cfg.database) as store:
        return fix_file(cfg, store, path, **kwargs)


def corrections(cfg: Config) -> list[tuple[str, str | None, str]]:
    with Store.open(cfg.database) as store:
        rows = store.conn.execute("SELECT field, old_value, new_value FROM corrections ORDER BY id")
        return [tuple(r) for r in rows]


def test_fix_category_moves_indexes_and_records(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"bill.txt": (b"electricity bill", answer("Other"))})
    report = fix(cfg, lib(downloads) / "Other" / "bill.txt", category="Finance")
    target = lib(downloads) / "Finance" / "bill.txt"
    assert report.destination == target and target.exists()
    assert not (lib(downloads) / "Other" / "bill.txt").exists()
    assert report.category == ("Other", "Finance") and report.name is None
    row = row_for(cfg, "bill.txt")
    assert (row.status, row.category, row.current_path) == (FileStatus.ORGANIZED, "Finance", target)
    assert corrections(cfg) == [("category", "Other", "Finance")]
    with Store.open(cfg.database) as store:
        [hit] = search(store, "bill")
    assert (hit.path, hit.category) == (str(target), "Finance")


def test_fix_is_undone_like_a_run(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"bill.txt": (b"electricity bill", answer("Other"))})
    fix(cfg, lib(downloads) / "Other" / "bill.txt", category="Finance", name="power")
    with Store.open(cfg.database) as store:
        assert len(store.current_corrections()) == 2
        report = undo_run(store, config=cfg)
        assert len(report.restored) == 1
        # Undone, the correction no longer teaches anything.
        assert store.current_corrections() == []
    row = row_for(cfg, "bill.txt")
    assert (row.status, row.category) == (FileStatus.ORGANIZED, "Other")
    assert row.current_path == lib(downloads) / "Other" / "bill.txt"
    assert row.current_path.exists()


def test_undo_file_of_a_fix(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"a.txt": (b"a", answer("Other", 0.5))})
    review = lib(downloads) / "_Review" / "a.txt"
    assert row_for(cfg, "a.txt").status is FileStatus.REVIEW
    report = fix(cfg, review, category="images")  # case-insensitive match
    assert report.destination == lib(downloads) / "Images" / "a.txt"
    assert report.category == (None, "Images")
    with Store.open(cfg.database) as store:
        undo_file(store, report.destination, config=cfg)
    row = row_for(cfg, "a.txt")
    assert (row.status, row.category, row.current_path) == (FileStatus.REVIEW, None, review)


def test_rename_keeps_the_extension_and_the_folder(downloads: Path, cfg: Config) -> None:
    organize(
        downloads, cfg, {"a.txt": (b"a", answer("Finance")), "b.txt": (b"b", answer("Other", 0.5))}
    )
    report = fix(cfg, lib(downloads) / "Finance" / "a.txt", name="october invoice")
    assert report.destination == lib(downloads) / "Finance" / "october invoice.txt"
    assert report.name == ("a.txt", "october invoice.txt") and report.category is None
    # A file in review can be renamed and stay there.
    review = fix(cfg, lib(downloads) / "_Review" / "b.txt", name="b.md")
    assert review.destination == lib(downloads) / "_Review" / "b.md"
    assert row_for(cfg, "b.txt").status is FileStatus.REVIEW
    assert corrections(cfg) == [
        ("name", "a.txt", "october invoice.txt"),
        ("name", "b.txt", "b.md"),
    ]


def test_taken_names_get_a_counter(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"a.txt": (b"a", answer("Other")), "b.txt": (b"b", answer("Finance"))})
    report = fix(cfg, lib(downloads) / "Other" / "a.txt", category="Finance", name="b")
    assert report.destination == lib(downloads) / "Finance" / "b-2.txt"
    assert (lib(downloads) / "Finance" / "b.txt").read_bytes() == b"b"


def test_changing_only_the_case_renames_in_place(downloads: Path, cfg: Config) -> None:
    if not case_insensitive(downloads):
        pytest.skip("the temporary folder's volume is case-sensitive; there is no clash")
    organize(downloads, cfg, {"bill.txt": (b"bill", answer("Other"))})
    other = lib(downloads) / "Other"
    report = fix(cfg, other / "bill.txt", name="Bill")
    assert report.destination == other / "Bill.txt"
    assert names(other) == ["Bill.txt"]
    assert corrections(cfg) == [("name", "bill.txt", "Bill.txt")]
    with Store.open(cfg.database) as store:
        undo_run(store, config=cfg)
    assert names(other) == ["bill.txt"]
    assert row_for(cfg, "bill.txt").current_path == other / "bill.txt"


def test_refusals_change_nothing(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"a.txt": (b"a", answer("Other")), "b.txt": (b"b", answer("Other"))})
    other = lib(downloads) / "Other"
    cases: list[tuple[Path, dict[str, Any], str]] = [
        (other / "a.txt", {}, "say what to change"),
        (downloads / "nope.txt", {"category": "Finance"}, "not a file flib organized"),
        (other / "a.txt", {"category": "Nope"}, "unknown category"),
        (other / "a.txt", {"category": "Other"}, "already there"),
        (other / "a.txt", {"name": "../escape"}, "not a plain file name"),
        (other / "a.txt", {"name": ".hidden"}, "not a plain file name"),
        (other / "a.txt", {"name": "x" * 300}, "longer than macOS allows"),
    ]
    for path, kwargs, message in cases:
        with pytest.raises(FixError, match=message):
            fix(cfg, path, **kwargs)
    (other / "b.txt").write_bytes(b"edited")
    with pytest.raises(FixError, match="changed or moved"):
        fix(cfg, other / "b.txt", category="Finance")
    with Store.open(cfg.database) as store:
        undo_run(store, config=cfg)
    with pytest.raises(FixError, match="is skipped"):
        fix(cfg, downloads / "a.txt", category="Finance")
    assert corrections(cfg) == []


def test_a_failed_move_is_journaled_as_failed(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"a.txt": (b"a", answer("Other"))})
    (lib(downloads) / "Finance").write_text("not a folder", encoding="utf-8")
    with pytest.raises(FixError, match="could not move"):
        fix(cfg, lib(downloads) / "Other" / "a.txt", category="Finance")
    with Store.open(cfg.database) as store:
        statuses = [r[0] for r in store.conn.execute("SELECT status FROM operations ORDER BY id")]
    assert statuses == ["done", "failed"]
    assert row_for(cfg, "a.txt").category == "Other" and corrections(cfg) == []


# --- learning: corrections become examples -----------------------------------------


def classify_new(downloads: Path, cfg: Config, name: str = "new.txt") -> FakeProvider:
    drop(downloads, {name: name.encode()})
    provider = FakeProvider(default=answer("Other"))
    pipeline.run(cfg, provider, simulate=True)
    return provider


def test_a_later_request_carries_the_correction(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"bill.txt": (b"electricity bill", answer("Other"))})
    fix(cfg, lib(downloads) / "Other" / "bill.txt", category="Finance", name="power")
    provider = classify_new(downloads, cfg)
    [rules] = provider.rules
    assert rules.examples == (
        Correction("name", "bill.txt", "power.txt", ".txt", False, "A other document."),
        Correction("category", "Other", "Finance", ".txt", False, "A other document."),
    )
    assert "electricity" not in repr(rules.examples)  # never file content


def test_a_new_correction_invalidates_cached_answers(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"a.txt": (b"a", answer("Other"))})
    drop(downloads, {"new.txt": b"new"})
    provider = FakeProvider(default=answer("Other"))
    pipeline.run(cfg, provider, simulate=True)
    pipeline.run(cfg, provider, simulate=True)
    assert len(provider.calls) == 1  # cached
    fix(cfg, lib(downloads) / "Other" / "a.txt", category="Finance")
    pipeline.run(cfg, provider, simulate=True)
    assert len(provider.calls) == 2


def test_examples_are_capped_newest_first(downloads: Path) -> None:
    cfg = make_cfg(downloads, provider={"max_examples": 2})
    organize(downloads, cfg, {f"f{i}.txt": (f"{i}".encode(), answer("Other")) for i in range(3)})
    for i in range(3):
        fix(cfg, lib(downloads) / "Other" / f"f{i}.txt", category="Finance")
    [rules] = classify_new(downloads, cfg).rules
    assert [e.new_value for e in rules.examples] == ["Finance", "Finance"]
    with Store.open(cfg.database) as store:
        newest = [r.current_name for r in store.current_corrections()]
    assert newest == ["f2.txt", "f1.txt", "f0.txt"]

    off = make_cfg(downloads, provider={"max_examples": 0})
    assert all(r.examples == () for r in classify_new(downloads, off, "other.txt").rules)


def test_never_send_and_sensitive_files_are_never_examples(downloads: Path) -> None:
    # Secret detection off, so a credential-like name gets organized at all.
    cfg = make_cfg(downloads, never_send=["*.secret"], sensitive={"detect_secrets": False})
    organize(
        downloads,
        cfg,
        {
            "plan.secret": (b"withheld", answer("Other")),
            "ok.txt": (b"fine", answer("Other")),
            "draft.txt": (b"draft", answer("Other")),
        },
    )
    fix(cfg, lib(downloads) / "Other" / "plan.secret", category="Finance")
    fix(cfg, lib(downloads) / "Other" / "ok.txt", category="Finance")
    # Renamed to a never_send name: neither name may teach anything.
    fix(cfg, lib(downloads) / "Other" / "draft.txt", name="draft.secret")
    with Store.open(cfg.database) as store:
        # A sensitive row (as reconcile may record a rename for) is left out too.
        row = next(r for r in store.all_files() if r.original_name == "ok.txt")
        store.add_correction(row.id, "name", "ok.txt", "ok.txt")
        store.set_file_status(row.id, FileStatus.SENSITIVE)
        assert "ok.txt" not in {r.current_name for r in store.current_corrections()}
    # Credential-like names never teach, whatever never_send says.
    organize(downloads, cfg, {"id_rsa_backup.txt": (b"k", answer("Other"))})
    fix(cfg, lib(downloads) / "Other" / "id_rsa_backup.txt", category="Finance")
    [rules] = classify_new(downloads, cfg).rules
    assert rules.examples == ()


# --- the command ---------------------------------------------------------------------


def test_cli_fix(tmp_path: Path, downloads: Path, cfg: Config) -> None:
    config_file = tmp_path / "c.yaml"
    config_file.write_text(
        f"inputs: [{downloads}]\nprovider: {{name: fake, model: x}}\n", encoding="utf-8"
    )
    organize(downloads, cfg, {"a.txt": (b"a", answer("Other"))})
    runner = CliRunner()
    path = str(lib(downloads) / "Other" / "a.txt")
    args = ["fix", path, "--category", "Finance", "--name", "receipt", "-c", str(config_file)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.stderr
    assert "moved   Other/a.txt  ->  Finance/receipt.txt" in result.stdout
    assert "learned category: Other -> Finance" in result.stdout
    assert "learned name: a.txt -> receipt.txt" in result.stdout
    undo = runner.invoke(app, ["undo", "-c", str(config_file)])
    assert undo.exit_code == 0, undo.stderr
    assert "1 restored" in undo.stdout
    assert row_for(cfg, "a.txt").category == "Other"
    failed = runner.invoke(app, ["fix", path, "-c", str(config_file)])
    assert failed.exit_code == 1 and "say what to change" in failed.stderr


# --- Finder tags (RF-36) --------------------------------------------------------------


def set_tags(path: Path, tags: list[str]) -> None:
    write_xattr(path, USER_TAGS, plistlib.dumps(tags, fmt=plistlib.FMT_BINARY))


def test_fix_swaps_the_tag_flib_added_and_undo_swaps_it_back(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"a.txt": (b"a", answer("Other"))})
    other = lib(downloads) / "Other" / "a.txt"
    set_tags(other, ["Work\n4", "Other"])  # the user's tag, then flib's
    report = fix(cfg, other, category="Finance")
    assert report.warnings == []
    assert read_tags(report.destination) == ["Work\n4", "Finance"]
    report = fix(cfg, report.destination, category="Images")
    assert read_tags(report.destination) == ["Work\n4", "Images"]
    with Store.open(cfg.database) as store:
        undo_run(store, config=cfg)
        assert read_tags(lib(downloads) / "Finance" / "a.txt") == ["Work\n4", "Finance"]
        undo_run(store, report.run_id - 1, config=cfg)  # the first fix
    assert read_tags(other) == ["Work\n4", "Other"]
    # A rename alone keeps the tags as they are.
    renamed = fix(cfg, other, name="b")
    assert read_tags(renamed.destination) == ["Work\n4", "Other"]


def test_fix_keeps_the_users_own_tag_and_honours_the_option(downloads: Path) -> None:
    cfg = make_cfg(downloads, finder_tags={"enabled": False})
    drop(downloads, {"a.txt": b"a"})
    set_tags(downloads / "a.txt", ["Other"])  # the user's, not flib's: no tag was recorded
    pipeline.run(cfg, FakeProvider(default=answer("Other")), simulate=False)
    report = fix(cfg, lib(downloads) / "Other" / "a.txt", category="Finance")
    assert read_tags(report.destination) == ["Other"]


def test_fix_tag_trouble_is_only_a_warning(tmp_path: Path, downloads: Path, cfg: Config) -> None:
    config_file = tmp_path / "c.yaml"
    config_file.write_text(
        f"inputs: [{downloads}]\nprovider: {{name: fake, model: x}}\n", encoding="utf-8"
    )
    organize(downloads, cfg, {"a.txt": (b"a", answer("Other"))})
    path = lib(downloads) / "Other" / "a.txt"
    write_xattr(path, USER_TAGS, b"\x00not a plist")
    args = ["fix", str(path), "--category", "Finance", "-c", str(config_file)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.stderr
    assert (lib(downloads) / "Finance" / "a.txt").exists()
    assert result.stdout.count("warning") == 2  # not removed, not set


# --- a crash in the middle of a fix (#47) --------------------------------------------


class Crash(BaseException):
    """Stands in for a power cut: not an Exception, so nothing in flib catches it."""


def crash_fix(
    cfg: Config, path: Path, patch: pytest.MonkeyPatch, where: str, crash: Any, **kwargs: Any
) -> None:
    with patch.context() as p, pytest.raises(Crash):
        p.setattr(where, crash)
        fix(cfg, path, **kwargs)
    with Store.open(cfg.database) as store:
        assert store.operations()[-1].status is OperationStatus.PLANNED


def raise_crash(*_args: Any, **_kwargs: Any) -> None:
    raise Crash


def move_then_crash(src: Path, dst: Path) -> None:
    move_no_replace(src, dst)
    raise Crash


def run_finished(cfg: Config, run_id: int) -> tuple[int, int]:
    with Store.open(cfg.database) as store:
        row = store.conn.execute(
            "SELECT finished_at IS NOT NULL, errors FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
    return int(row[0]), int(row[1])


@pytest.mark.parametrize(
    "where",
    ["file_librarian.fix.ensure_folder", "file_librarian.fix.move_no_replace"],
    ids=["before the folder", "before the rename"],
)
def test_recovery_fails_a_fix_that_never_moved(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    organize(downloads, cfg, {"a.txt": (b"a", answer("Other"))})
    source = lib(downloads) / "Other" / "a.txt"
    before = row_for(cfg, "a.txt")
    crash_fix(cfg, source, monkeypatch, where, raise_crash, category="Finance", name="b")
    with Store.open(cfg.database) as store:
        assert recover(store, cfg) == 1
        op = store.operations()[-1]
    assert op.status is OperationStatus.FAILED
    # The fix never happened: the file and its row are exactly as they were.
    assert row_for(cfg, "a.txt") == before and source.read_bytes() == b"a"
    assert corrections(cfg) == []
    assert run_finished(cfg, op.run_id) == (1, 1)
    assert read_tags(source) == ["Other"]


def test_recovery_finishes_a_fix_that_moved(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    organize(downloads, cfg, {"a.txt": (b"a", answer("Other"))})
    source = lib(downloads) / "Other" / "a.txt"
    where = "file_librarian.fix.move_no_replace"
    crash_fix(cfg, source, monkeypatch, where, move_then_crash, category="Finance", name="b")
    target = lib(downloads) / "Finance" / "b.txt"
    with Store.open(cfg.database) as store:
        assert recover(store, cfg) == 1
        op = store.operations()[-1]
        [hit] = search(store, "b")
    assert (op.status, op.dst_path, op.finder_tag) == (OperationStatus.DONE, target, "Finance")
    row = row_for(cfg, "a.txt")
    assert (row.status, row.category, row.current_path) == (FileStatus.ORGANIZED, "Finance", target)
    assert (hit.path, hit.category) == (str(target), "Finance")
    assert corrections(cfg) == [("category", "Other", "Finance"), ("name", "a.txt", "b.txt")]
    assert read_tags(target) == ["Finance"]
    assert run_finished(cfg, op.run_id) == (1, 0)
    # And it is undone like any finished fix.
    with Store.open(cfg.database) as store:
        undo_run(store, config=cfg)
    assert read_tags(source) == ["Other"]
    assert row_for(cfg, "a.txt").category == "Other"


def test_recovery_finishes_a_fix_cut_off_while_writing_the_index(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    organize(downloads, cfg, {"a.txt": (b"a", answer("Other"))})
    source = lib(downloads) / "Other" / "a.txt"
    # The tags are already swapped; the transaction writing the index rolls back.
    where = "file_librarian.fix.Store.add_correction"
    crash_fix(cfg, source, monkeypatch, where, raise_crash, category="Finance")
    target = lib(downloads) / "Finance" / "a.txt"
    assert read_tags(target) == ["Finance"]
    assert row_for(cfg, "a.txt").current_path == source
    with Store.open(cfg.database) as store:
        assert recover(store) == 1  # without the configuration, tags are left as they are
        op = store.operations()[-1]
    assert (op.status, op.finder_tag) == (OperationStatus.DONE, "Finance")
    assert row_for(cfg, "a.txt").current_path == target
    assert corrections(cfg) == [("category", "Other", "Finance")]
    assert read_tags(target) == ["Finance"]


def test_recovery_does_not_mistake_a_case_only_rename_for_done(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not case_insensitive(downloads):
        pytest.skip("the temporary folder's volume is case-sensitive; there is no clash")
    organize(downloads, cfg, {"bill.txt": (b"bill", answer("Other"))})
    source = lib(downloads) / "Other" / "bill.txt"
    where = "file_librarian.fix.move_no_replace"
    crash_fix(cfg, source, monkeypatch, where, raise_crash, name="Bill")
    # "Bill.txt" opens "bill.txt" here, but the rename never happened.
    with Store.open(cfg.database) as store:
        assert recover(store, cfg) == 1
        assert store.operations()[-1].status is OperationStatus.FAILED
    assert row_for(cfg, "bill.txt").current_path == source
    assert names(source.parent) == ["bill.txt"]


def test_cli_fix_recovers_an_interrupted_fix_first(
    tmp_path: Path, downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    organize(downloads, cfg, {"a.txt": (b"a", answer("Other")), "b.txt": (b"b", answer("Other"))})
    other = lib(downloads) / "Other"
    where = "file_librarian.fix.move_no_replace"
    crash_fix(cfg, other / "a.txt", monkeypatch, where, move_then_crash, category="Finance")
    config_file = tmp_path / "c.yaml"
    config_file.write_text(
        f"inputs: [{downloads}]\nprovider: {{name: fake, model: x}}\n", encoding="utf-8"
    )
    args = ["fix", str(other / "b.txt"), "--category", "Finance", "-c", str(config_file)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.stderr
    assert row_for(cfg, "a.txt").category == "Finance"
    assert read_tags(lib(downloads) / "Finance" / "a.txt") == ["Finance"]
