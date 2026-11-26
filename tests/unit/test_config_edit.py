"""Changing the configuration file in place (#117): only the keys asked for.

Every edit is checked by parsing the result; these tests pin down that comments,
blank lines and the order of keys survive, that each kind of layout is handled
or refused (never half-done), and that the file is written atomically with a
backup, mode 0600, and never through a link.
"""

from __future__ import annotations

import os
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from file_librarian import config_edit
from file_librarian.config_edit import Append, Delete, EditError, Set, edit_text

FILE = """\
# head comment
language: en  # names in English
inputs:
  - "~/Downloads"
  # the Desktop, with rules
  - path: "~/Desktop"
    only: ["*.pdf", "*.png"]
    min_age_days: 7

provider:
  # who classifies
  name: fake
  model: x   # model note

vision:
  enabled: false  # images stay here
schedule:
  time: "12:30"  # the daily run
categories:
  - name: Finance
    hint: |
      money matters
  - name: Images
due:
  kinds: [bill, tax]
limits:
  max_cost_usd_per_day: 3
# tail comment
"""


def lines_kept(before: str, after: str, *, except_: tuple[str, ...] = ()) -> None:
    """Every line of ``before`` is still in ``after``, but those that hold ``except_``."""
    for line in before.splitlines():
        if not any(word in line for word in except_):
            assert line in after.splitlines(), line


def test_a_scalar_is_changed_where_it_stands() -> None:
    after = edit_text(FILE, [Set(("vision", "enabled"), True)])
    assert "  enabled: true  # images stay here" in after
    lines_kept(FILE, after, except_=("enabled",))
    assert yaml.safe_load(after)["vision"] == {"enabled": True}


def test_a_missing_key_goes_at_the_end_of_its_section() -> None:
    after = edit_text(FILE, [Set(("schedule", "mode"), "watch")])
    assert '  time: "12:30"  # the daily run\n  mode: "watch"\ncategories:' in after
    lines_kept(FILE, after)


def test_a_missing_section_is_added_with_its_keys() -> None:
    after = edit_text(FILE, [Set(("search", "semantic", "enabled"), True)])
    assert "search:\n  semantic:\n    enabled: true\n# tail comment" in after
    lines_kept(FILE, after)


def test_an_empty_section_gets_its_keys_under_it() -> None:
    text = "provider: {name: fake, model: x}\nschedule:\nvision: {enabled: false}\n"
    after = edit_text(text, [Set(("schedule", "mode"), "both")])
    assert after == (
        'provider: {name: fake, model: x}\nschedule:\n  mode: "both"\nvision: {enabled: false}\n'
    )


def test_a_block_scalar_is_replaced_whole() -> None:
    after = edit_text(FILE, [Set(("categories", 0, "hint"), "new hint")])
    assert '    hint: "new hint"\n  - name: Images' in after
    assert "money matters" not in after


def test_a_list_item_is_removed_with_its_lines() -> None:
    after = edit_text(FILE, [Delete(("categories", 1))])
    assert "Images" not in after
    assert "      money matters\ndue:" in after


def test_items_are_appended_in_the_list_s_style() -> None:
    entry = {"name": "Docs", "hint": "x", "rename": False}
    after = edit_text(FILE, [Append(("categories",), entry)])
    assert '  - name: Images\n  - name: "Docs"\n    hint: "x"\n    rename: false\n' in after
    after = edit_text(FILE, [Append(("inputs",), "~/Other")])
    assert '    min_age_days: 7\n  - "~/Other"\n\nprovider:' in after


def test_an_inline_list_is_written_again_inline() -> None:
    after = edit_text(FILE, [Set(("due", "kinds"), ["bill"])])
    assert '  kinds: ["bill"]\n' in after
    after = edit_text(FILE, [Delete(("inputs", 1, "only"))])
    assert "only" not in after and "    min_age_days: 7" in after


def test_an_inline_mapping_is_written_again_inline() -> None:
    text = 'inputs: ["/tmp/a"]\nprovider: {name: fake, model: x}  # who\n'
    after = edit_text(text, [Set(("provider", "model"), "y"), Append(("inputs",), "~/D")])
    assert after == 'inputs: ["/tmp/a", "~/D"]\nprovider: {name: "fake", model: "y"}  # who\n'


def test_a_block_list_is_replaced_as_a_block_list() -> None:
    text = "due:\n  kinds:\n    - bill\n    - tax  # taxes\nlimits: {}\n"
    after = edit_text(text, [Set(("due", "kinds"), ["bill", "receipt"])])
    assert after == 'due:\n  kinds:\n    - "bill"\n    - "receipt"  # taxes\nlimits: {}\n'


def test_removing_the_only_key_leaves_an_empty_section() -> None:
    after = edit_text(FILE, [Delete(("limits", "max_cost_usd_per_day"))])
    assert "limits:\n  {}\n# tail comment" in after
    assert yaml.safe_load(after)["limits"] == {}
    text = "a:\n  - x\nb: 1\n"
    assert yaml.safe_load(edit_text(text, [Delete(("a", 0))])) == {"a": [], "b": 1}


def test_deleting_what_is_not_there_changes_nothing() -> None:
    assert edit_text(FILE, [Delete(("search", "semantic"))]) == FILE


def test_several_edits_at_once() -> None:
    after = edit_text(
        FILE,
        [
            Set(("provider", "model"), "y"),
            Set(("limits", "max_cost_usd_per_run"), 2.5),
            Set(("language",), "pt-BR"),
        ],
    )
    data = yaml.safe_load(after)
    assert data["provider"]["model"] == "y" and data["language"] == "pt-BR"
    assert data["limits"] == {"max_cost_usd_per_day": 3, "max_cost_usd_per_run": 2.5}
    assert '  model: "y"   # model note' in after


def test_a_value_inside_a_new_collection_is_written_as_a_block() -> None:
    after = edit_text("a: 1\n", [Set(("b",), {"c": [1, 2], "d": [{"e": 1}]})])
    assert after == "a: 1\nb:\n  c: [1, 2]\n  d:\n    - e: 1\n"
    after = edit_text("a: 1", [Set(("b",), 2)])  # no newline at the end
    assert after == "a: 1\nb: 2\n"


def test_a_key_that_needs_quotes_gets_them() -> None:
    after = edit_text("a: 1\n", [Set(("odd key",), True)])
    assert '"odd key": true' in after


@pytest.mark.parametrize(
    ("text", "edit"),
    [
        ("vision: true\n", Set(("vision", "enabled"), True)),  # a scalar where a section is
        ("inputs:\n  - - a\n  - b\n", Delete(("inputs", 0))),  # a list in a list
        ("a:\n- x\n- y\n", Set(("a", 5), "z")),  # an index past the end
        ("{a: 1}\n", Set(("a",), 2)),  # an inline document
        ("a: !!str 1\n", Set(("b",), 1)),  # parses, but the check still holds
    ],
)
def test_what_cannot_be_done_exactly_is_refused(text: str, edit: Any) -> None:
    try:
        after = edit_text(text, [edit])
    except EditError:
        return
    expected = config_edit.apply_data(yaml.safe_load(text), [edit])
    assert yaml.safe_load(after) == expected  # done, then exactly


@pytest.mark.parametrize("text", ["a: [1\n", "- a\n", "a: 1\n---\nb: 2\n", ""])
def test_a_file_that_is_not_one_mapping_is_refused(text: str) -> None:
    with pytest.raises(EditError):
        edit_text(text, [Set(("a",), 1)])


def test_a_yaml_error_names_the_place_and_never_quotes_the_file() -> None:
    with pytest.raises(EditError) as caught:
        config_edit.load("a: 1\nsecret-looking: [sk-ant-xyz\n")
    assert "line" in str(caught.value) and "sk-ant" not in str(caught.value)


@pytest.mark.parametrize(
    ("value", "text"),
    [(None, "null"), (True, "true"), (False, "false"), (3, "3"), (2.5, "2.5"), ('a"b', '"a\\"b"')],
)
def test_scalars(value: Any, text: str) -> None:
    assert config_edit.scalar(value) == text
    assert yaml.safe_load(f"k: {text}")["k"] == value


@pytest.mark.parametrize("value", [1e-9, float("inf"), object()])
def test_values_it_cannot_write_are_refused(value: Any) -> None:
    with pytest.raises(EditError):
        config_edit.scalar(value)


def test_block_refuses_a_scalar() -> None:
    with pytest.raises(EditError):
        config_edit.block(1, 0)


def test_unicode_survives() -> None:
    after = edit_text("a: 1\n", [Set(("hint",), "Ação, 日本 — ok")])
    assert yaml.safe_load(after)["hint"] == "Ação, 日本 — ok"
    assert "Ação" in after


def test_crlf_files_are_edited() -> None:
    text = "a: 1\r\nb:\r\n  c: 2\r\n"
    after = edit_text(text, [Set(("b", "c"), 3), Set(("b", "d"), 4)])
    assert yaml.safe_load(after) == {"a": 1, "b": {"c": 3, "d": 4}}


# --- the file --------------------------------------------------------------------------


def config_file(tmp_path: Path, text: str = FILE) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_write_keeps_a_backup_and_writes_atomically(tmp_path: Path) -> None:
    path = config_file(tmp_path)
    old = path.read_bytes()
    backup = config_edit.write(path, old, "new: 1\n", now=datetime(2026, 1, 2, 3, 4, 5))
    assert path.read_text(encoding="utf-8") == "new: 1\n"
    assert backup.name == "config.yaml.backup-20260102-030405"
    assert backup.read_bytes() == old
    assert mode(path) == 0o600 and mode(backup) == 0o600
    assert not list(tmp_path.glob(".*.tmp"))
    again = config_edit.write(path, b"new: 1\n", "new: 2\n", now=datetime(2026, 1, 2, 3, 4, 5))
    assert again.name.endswith("-02") and config_edit.backups(path)[0] == again


def test_write_keeps_the_latest_backups_only(tmp_path: Path) -> None:
    path = config_file(tmp_path)
    for i in range(config_edit.KEEP_BACKUPS + 3):
        config_edit.write(path, path.read_bytes(), f"n: {i}\n", now=datetime(2026, 1, 1, 0, 0, i))
    found = config_edit.backups(path)
    assert len(found) == config_edit.KEEP_BACKUPS
    assert found[0].name.endswith("000012") and found[-1].name.endswith("000003")


def test_write_never_replaces_a_link_or_a_folder(tmp_path: Path) -> None:
    real = config_file(tmp_path)
    link = tmp_path / "linked.yaml"
    link.symlink_to(real)
    with pytest.raises(EditError, match="symbolic link"):
        config_edit.write(link, real.read_bytes(), "x: 1\n")
    assert real.read_text(encoding="utf-8") == FILE and link.is_symlink()
    (tmp_path / "folder.yaml").mkdir()
    with pytest.raises(EditError, match="not a plain file"):
        config_edit.write(tmp_path / "folder.yaml", b"", "x: 1\n")
    with pytest.raises(EditError, match="missing"):
        config_edit.write(tmp_path / "none.yaml", b"", "x: 1\n")


def test_a_failed_write_leaves_the_file_and_no_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = config_file(tmp_path)

    def broken(self: Path, target: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", broken)
    with pytest.raises(OSError):
        config_edit.write(path, path.read_bytes(), "x: 1\n")
    assert path.read_text(encoding="utf-8") == FILE
    assert not list(tmp_path.glob(".*.tmp"))


def test_a_planted_temporary_or_backup_link_is_never_followed(tmp_path: Path) -> None:
    path = config_file(tmp_path)
    target = tmp_path / "elsewhere"
    target.write_text("keep", encoding="utf-8")
    stamp = datetime(2026, 1, 2, 3, 4, 5)
    (tmp_path / "config.yaml.backup-20260102-030405").symlink_to(target)
    backup = config_edit.write(path, path.read_bytes(), "x: 1\n", now=stamp)
    assert backup.name.endswith("-02")
    assert target.read_text(encoding="utf-8") == "keep"


def test_backups_list_only_the_users_plain_files(tmp_path: Path) -> None:
    path = config_file(tmp_path)
    (tmp_path / "config.yaml.backup-20260101-000000").mkdir()
    (tmp_path / "config.yaml.backup-20260101-000001").symlink_to(path)
    assert config_edit.backups(path) == []
    assert config_edit.backups(tmp_path / "missing" / "config.yaml") == []


def test_the_pin_is_the_files_bytes() -> None:
    assert config_edit.pin(b"a") == config_edit.pin(b"a") != config_edit.pin(b"b")
    assert len(config_edit.pin(b"")) == config_edit.PIN_LENGTH


def test_writing_syncs_the_folder_and_survives_a_folder_it_cannot_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = config_file(tmp_path)
    real_open = os.open

    def no_folders(target: Any, flags: int, *args: Any) -> int:
        if Path(target) == tmp_path:
            raise PermissionError("no")
        return real_open(target, flags, *args)

    monkeypatch.setattr(os, "open", no_folders)
    config_edit.write(path, path.read_bytes(), "x: 1\n")
    assert path.read_text(encoding="utf-8") == "x: 1\n"


@pytest.mark.parametrize(
    "text",
    [
        "base: &b {enabled: false}\nvision: *b\n",
        "vision: &v\n  enabled: false\nsearch:\n  semantic: *v\n",
        "a: &x 1\nb: *x\n",
    ],
)
def test_a_file_with_aliases_is_left_alone(text: str) -> None:
    with pytest.raises(EditError, match="aliases"):
        edit_text(text, [Set(("vision", "enabled"), True)])


def test_the_result_is_always_checked_against_the_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever the splicing does, a text that does not parse to the asked-for data is
    never returned."""
    monkeypatch.setattr(config_edit._Text, "apply", lambda self, edit, after: "vision: {}\n")
    with pytest.raises(EditError, match="cannot change safely"):
        edit_text(FILE, [Set(("vision", "enabled"), True)])
    monkeypatch.setattr(config_edit._Text, "apply", lambda self, edit, after: "a: [1\n")
    with pytest.raises(EditError, match="not be valid YAML"):
        edit_text(FILE, [Set(("vision", "enabled"), True)])
