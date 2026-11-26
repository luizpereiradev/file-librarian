# ruff: noqa: F811 - the fixtures of test_ui_settings are imported, and used by name
"""The Settings page after its review (#117, PR #130): one test per finding.

The reviewer's probes asserted each bug; they are inverted here. The forms are
submitted as a browser would submit the page as shown (``_Forms`` reads every
field, with a text field's line breaks dropped), so "the user changed one thing"
is literally one field changed.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest
import yaml

from file_librarian import config_edit, keychain, scheduler
from file_librarian.config import ConfigError, load_config, parse_config
from file_librarian.locales import LOCALES
from file_librarian.lock import instance_lock
from file_librarian.ui import settings
from file_librarian.ui.server import Ui
from tests._helpers import UiClient
from tests.unit.test_ui import (  # noqa: F401 - fixtures: every server is checked the same way
    _Collector,
    events,
    no_hang,
    nothing_left_behind,
)
from tests.unit.test_ui_pages import assert_safe
from tests.unit.test_ui_settings import (  # noqa: F401 - fixtures
    FILE,
    cfg,
    client,
    page,
    path,
    post,
    serve,
    start,
)


class _Forms(HTMLParser):
    """Each <form>: (action, the fields a browser sends with nobody touching it)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[tuple[str, list[tuple[str, str]]]] = []
        self._cur: list[tuple[str, str]] | None = None
        self._select: str | None = None
        self._first: str | None = None
        self._chosen: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "form":
            self._cur = []
            self.forms.append((a.get("action") or "", self._cur))
        elif self._cur is None:
            return
        elif tag == "input" and a.get("name"):
            kind, name, value = a.get("type") or "text", a["name"] or "", a.get("value") or ""
            if kind in ("checkbox", "radio"):
                if "checked" in a:
                    self._cur.append((name, value or "on"))
            else:
                if kind == "text":  # a browser drops a text field's line breaks
                    value = value.replace("\r", "").replace("\n", "")
                self._cur.append((name, value))
        elif tag == "select":
            self._select, self._first, self._chosen = a.get("name"), None, None
        elif tag == "option" and self._select is not None:
            if self._first is None:
                self._first = a.get("value") or ""
            if "selected" in a:
                self._chosen = a.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "select" and self._cur is not None and self._select is not None:
            chosen = self._chosen if self._chosen is not None else self._first
            self._cur.append((self._select, chosen or ""))
            self._select = None
        elif tag == "form":
            self._cur = None


def forms(text: str) -> list[tuple[str, list[tuple[str, str]]]]:
    parser = _Forms()
    parser.feed(text)
    return parser.forms


def find_form(text: str, **match: str) -> tuple[str, list[tuple[str, str]]]:
    for action, fields in forms(text):
        if all(dict(fields).get(k) == v for k, v in match.items()):
            return action, fields
    raise AssertionError(f"no form {match}")


def replace(fields: list[tuple[str, str]], name: str, value: str | None) -> list[tuple[str, str]]:
    out = [(k, v) for k, v in fields if k != name]
    return out if value is None else [*out, (name, value)]


def submit(c: UiClient, action: str, fields: list[tuple[str, str]]) -> tuple[str, str]:
    """Post a form as the browser does; (where the redirect goes, the page there)."""
    reply = c.request(
        "POST",
        action,
        body=urlencode(fields).encode(),
        headers={
            "Origin": c.origin,
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert reply.status == 303, (reply.status, reply.text)
    location = reply.headers["Location"]
    text = c.get(location.split("#", 1)[0]).text
    assert_safe(text)
    return location, text


def fresh(serve: list[Ui], path: Path) -> UiClient:
    return start(serve, load_config(path), path)


def append(path: Path, extra: str) -> None:
    path.write_text(path.read_text(encoding="utf-8") + extra, encoding="utf-8")


def data(path: Path) -> dict[str, Any]:
    found = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(found, dict)
    return found


def changed_list(text: str) -> str:
    found = re.search(r'aria-label="What changed">(.*?)</ul>', text, re.S)
    assert found is not None, "no list of changes"
    return re.sub(r"<[^>]+>", "", found.group(1))


CATS = """
categories:
  - name: Bills
    hint: |-
      Utility bills.
      Not receipts.
  - name: Work
    hint: "  indented hint"
"""


# --- R1: another configuration file's schedule -----------------------------------------


@pytest.fixture
def other_schedule(fake_home: Path, downloads: Path) -> Path:
    """The real schedule, installed for another configuration file."""
    main = fake_home / ".config" / "file-librarian" / "main.yaml"
    main.write_text(FILE.format(downloads=json.dumps(str(downloads))), encoding="utf-8")
    scheduler.install_for(main, load_config(main), "12:30", "both")
    return main


def agents_config() -> set[str]:
    return {
        plistlib.loads(scheduler.plist_path(label).read_bytes())["EnvironmentVariables"][
            "FLIB_CONFIG"
        ]
        for label in (scheduler.LABEL, scheduler.WATCH_LABEL)
    }


def test_an_input_change_never_takes_another_files_schedule(
    serve: list[Ui], path: Path, fake_home: Path, other_schedule: Path
) -> None:
    c = fresh(serve, path)
    (fake_home / "Desktop").mkdir()
    _, text = submit(c, *find_form(page(c), section="input-add"))
    assert "not changed: the schedule uses another configuration file" in text
    assert agents_config() == {str(other_schedule.resolve())}


def test_the_schedule_of_another_file_is_moved_only_when_asked(
    serve: list[Ui], path: Path, other_schedule: Path
) -> None:
    c = fresh(serve, path)
    text = page(c)
    assert "The installed schedule uses another configuration file" in text
    assert "~/.config/file-librarian/main.yaml" in text
    action, fields = next(f for f in forms(text) if f[0] == "/settings/schedule")
    assert "takeover" not in dict(fields)  # unticked by default
    for mode in ("daily", "off"):
        _, after = submit(c, action, replace(fields, "mode", mode))
        assert "Nothing was changed in launchd" in after
        assert agents_config() == {str(other_schedule.resolve())}
    _, after = submit(c, action, replace(replace(fields, "mode", "daily"), "takeover", "1"))
    assert "Daily run scheduled" in after
    assert scheduler.scheduled_config() == path.resolve()


# --- R2: fields the user did not touch -------------------------------------------------


def test_an_untouched_multiline_hint_is_kept_and_shown_as_text(serve: list[Ui], path: Path) -> None:
    append(path, CATS)
    c = fresh(serve, path)
    text = page(c)
    assert "It has line breaks or is long" in text
    action, fields = find_form(text, section="category-edit", category="Bills")
    assert "hint" not in dict(fields)  # no field that would flatten it
    _, after = submit(c, action, replace(fields, "layout", "year"))
    bills = data(path)["categories"][0]
    assert bills == {"name": "Bills", "hint": "Utility bills.\nNot receipts.", "layout": "{year}"}
    assert "hint" not in changed_list(after)
    assert "Category Bills layout: the default → {year}" in changed_list(after)


def test_a_hint_with_spaces_at_its_ends_is_left_alone(serve: list[Ui], path: Path) -> None:
    append(path, CATS)
    c = fresh(serve, path)
    action, fields = find_form(page(c), section="category-edit", category="Work")
    submit(c, action, replace(fields, "rename", None))
    assert data(path)["categories"][1] == {
        "name": "Work",
        "hint": "  indented hint",
        "rename": False,
    }


def test_what_changed_is_said_field_by_field(serve: list[Ui], path: Path) -> None:
    append(path, CATS)
    c = fresh(serve, path)
    action, fields = find_form(page(c), section="category-edit", category="Work")
    _, text = submit(c, action, replace(fields, "hint", "office papers"))
    assert changed_list(text) == "Category Work hint:   indented hint → office papers"
    action, fields = find_form(page(c), section="category-add")
    _, text = submit(c, action, replace(fields, "name", "Taxes"))
    assert changed_list(text) == "Category Taxes: added"
    action, fields = find_form(page(c), section="category-edit", category="Taxes")
    _, text = submit(c, action, replace(fields, "name", "Tax"))
    assert changed_list(text) == "Category Taxes: renamed to Tax"


def test_categories_from_the_language_are_said_once(client: UiClient, path: Path) -> None:
    action, fields = find_form(page(client), section="category-add")
    _, text = submit(client, action, replace(fields, "name", "Taxes"))
    assert changed_list(text) == "Category Taxes: added"
    assert isinstance(data(path)["categories"], list)


# --- R3: amounts shown without loss ----------------------------------------------------


@pytest.mark.parametrize(("written", "shown"), [("12.34567", "12.34567"), ("0.00001", "0.00001")])
def test_an_untouched_amount_is_kept_to_its_last_digit(
    serve: list[Ui], path: Path, written: str, shown: str
) -> None:
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "max_cost_usd_per_run: 1.00", f"max_cost_usd_per_run: {written}"
        ),
        encoding="utf-8",
    )
    c = fresh(serve, path)
    action, fields = find_form(page(c), section="costs")
    assert dict(fields)["per_run"] == shown
    _, text = submit(c, action, replace(fields, "per_day", "none"))
    assert "Saved." in text
    assert data(path)["limits"] == {
        "max_cost_usd_per_run": float(written),
        "max_cost_usd_per_day": None,
    }
    action, fields = find_form(page(c), section="costs")
    _, text = submit(c, action, replace(replace(fields, "per_day", "amount"), "day_amount", "2"))
    action, fields = find_form(page(c), section="costs")
    assert dict(fields)["day_amount"] == "2.0"
    assert settings.NOTHING in submit(c, action, fields)[1]


def test_amount_text_never_has_an_exponent() -> None:
    assert settings.amount_text(1e-05) == "0.00001"
    assert settings.amount_text(12.34567) == "12.34567"
    assert settings.amount_text(None) == ""


# --- R4: only flib's own backups -------------------------------------------------------


def test_a_users_own_backup_like_files_are_never_listed_or_deleted(
    serve: list[Ui], path: Path
) -> None:
    mine = [
        path.with_name(path.name + suffix)
        for suffix in (".backup-1-before-upgrade", ".backup-old", ".backup-20260101-000000")
    ]
    for f in mine:
        f.write_text("# my own copy\n", encoding="utf-8")
    c = fresh(serve, path)
    for i in range(config_edit.KEEP_BACKUPS + 2):
        action, fields = find_form(page(c), section="reading")
        submit(c, action, replace(fields, "vision", None if i % 2 else "1"))
    assert all(f.exists() for f in mine)
    found = config_edit.backups(path)
    assert len(found) == config_edit.KEEP_BACKUPS and not set(found) & set(mine)
    assert all(re.fullmatch(r"config\.yaml\.backup-\d{8}-\d{6}Z(-\d{2,})?", f.name) for f in found)


def test_backups_are_ordered_by_utc_time_then_sequence(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("a: 1\n", encoding="utf-8")
    moment = datetime(2026, 3, 29, 1, 30, tzinfo=UTC)
    first = config_edit.write(path, b"a: 1\n", "a: 2\n", now=moment)
    second = config_edit.write(path, b"a: 2\n", "a: 3\n", now=moment)
    tenth = [
        config_edit.write(path, path.read_bytes(), f"a: {n}\n", now=moment) for n in range(4, 13)
    ][-1]
    assert first.name.endswith("Z") and second.name.endswith("Z-02") and tenth.name.endswith("-11")
    assert config_edit.backups(path)[0] == tenth  # -11 after -02: by number, not by text
    assert config_edit.stamp_of(path, tmp_path / "config.yaml.backup-x") is None


# --- R5: commands to paste -------------------------------------------------------------


def test_a_refusals_command_is_quoted_and_names_this_file(
    serve: list[Ui], path: Path, fake_home: Path
) -> None:
    name = "Bills$(touch PWNED) `x`"
    other = fake_home / "my configs" / "work.yaml"
    other.parent.mkdir()
    other.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    append(other, f"\ncategories:\n  - name: {json.dumps(name)}\n  - name: Work\n")
    folder = load_config(other).output_root / name
    folder.mkdir(parents=True)
    (folder / "a.pdf").write_bytes(b"%PDF")
    c = fresh(serve, other)
    _, text = submit(c, *find_form(page(c), section="category-remove", category=name))
    code = re.findall(r"<code>(.*?)</code>", text)
    expected = f"flib reprocess -c '{other}' --remove-category 'Bills$(touch PWNED) `x`'"
    import html as html_mod

    assert expected in [html_mod.unescape(x) for x in code]
    assert "<code>x</code>" not in text  # the name's backticks stay text


def test_the_default_file_needs_no_c(fake_home: Path) -> None:
    default = fake_home / ".config" / "file-librarian" / "config.yaml"
    assert settings.command(default, "reprocess", "--remove-category", "A B") == (
        "`flib reprocess --remove-category 'A B'`"
    )


# --- R6: due.kinds keeps the file's order ----------------------------------------------


def test_kinds_keep_their_order(serve: list[Ui], path: Path) -> None:
    append(path, "\ndue:\n  kinds: [tax, bill]  # my order\n")
    c = fresh(serve, path)
    action, fields = find_form(page(c), section="reminders")
    submit(c, action, replace(fields, "notify", None))
    assert data(path)["due"]["kinds"] == ["tax", "bill"]
    assert "  kinds: [tax, bill]  # my order" in path.read_text(encoding="utf-8")
    action, fields = find_form(page(c), section="reminders")
    submit(c, action, [*fields, ("kinds", "receipt")])
    assert data(path)["due"]["kinds"] == ["tax", "bill", "receipt"]


# --- R7: a hand edit while saving ------------------------------------------------------


def test_a_hand_edit_while_saving_is_kept_and_the_save_refused(
    serve: list[Ui], path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = fresh(serve, path)
    action, fields = find_form(page(c), section="reading")
    real = config_edit._create

    def racing(target: Path, content: bytes) -> None:
        real(target, content)
        if target.name.endswith(".tmp"):  # an editor saves just before the rename
            path.write_text(path.read_text(encoding="utf-8") + "# by hand\n", encoding="utf-8")

    monkeypatch.setattr(config_edit, "_create", racing)
    _, text = submit(c, action, replace(fields, "vision", "1"))
    assert "changed since this page was shown" in text
    assert path.read_text(encoding="utf-8").endswith("# by hand\n")
    assert data(path)["vision"] == {"enabled": False}
    assert config_edit.backups(path) == []
    assert not list(path.parent.glob(".*.tmp"))


def test_write_refuses_bytes_that_are_not_the_ones_it_was_given(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    target.write_text("a: 2\n", encoding="utf-8")
    with pytest.raises(config_edit.Changed):
        config_edit.write(target, b"a: 1\n", "a: 3\n")
    assert target.read_text(encoding="utf-8") == "a: 2\n" and config_edit.backups(target) == []


# --- R8 --------------------------------------------------------------------------------


@pytest.mark.parametrize("time", ["12:\u0663\u0660", "09:\uff13\uff10", "\u0661\u0662:30"])
def test_a_time_takes_ascii_digits_only(serve: list[Ui], path: Path, time: str) -> None:
    c = fresh(serve, path)
    action, fields = next(f for f in forms(page(c)) if f[0] == "/settings/schedule")
    _, text = submit(c, action, replace(replace(fields, "mode", "daily"), "time", time))
    assert "Daily run at needs a 24-hour time" in text
    assert data(path)["schedule"] == {"time": "12:30"}
    with pytest.raises(ConfigError):
        parse_config({"provider": {"name": "fake", "model": "x"}, "schedule": {"time": time}})


def test_the_page_asks_whether_there_is_a_key_never_for_it(
    serve: list[Ui], path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path.write_text(
        path.read_text(encoding="utf-8").replace("name: fake", "name: anthropic"), "utf-8"
    )
    keychain.write("sk-ant-api03-FAKE", keychain.ACCOUNT)
    read: list[str] = []
    monkeypatch.setattr(keychain, "read", lambda account=keychain.ACCOUNT: read.append(account))
    c = fresh(serve, path)
    assert "Anthropic API key: set (in the Keychain)" in page(c)
    action, fields = next(f for f in forms(page(c)) if f[0] == "/settings/schedule")
    submit(c, action, replace(fields, "mode", "daily"))
    assert read == []
    assert keychain.exists(keychain.ACCOUNT) and not keychain.exists(keychain.EMBEDDINGS_ACCOUNT)


def test_a_hard_linked_file_is_never_replaced(serve: list[Ui], path: Path, tmp_path: Path) -> None:
    twin = tmp_path / "twin.yaml"
    os.link(path, twin)
    c = fresh(serve, path)
    before = path.read_bytes()
    action, fields = find_form(page(c), section="reading")
    _, text = submit(c, action, replace(fields, "vision", "1"))
    assert "hard link" in text
    assert path.read_bytes() == before == twin.read_bytes()
    assert path.stat().st_ino == twin.stat().st_ino


# --- R9: the checks the mutants showed untested ----------------------------------------


def restore_form(c: UiClient) -> list[tuple[str, str]]:
    return next(f for f in forms(c.get("/settings/restore").text) if f[0] == "/settings/restore")[1]


def test_restore_checks_the_files_pin_on_its_own(client: UiClient, path: Path) -> None:
    submit(client, *replace_form(client, "reading", "vision", "1"))
    fields = restore_form(client)
    stale = replace(fields, "pin", "0" * 20)
    _, text = submit(client, "/settings/restore", stale)
    assert "changed since this page was shown" in text
    assert data(path)["vision"] == {"enabled": True}


def replace_form(
    c: UiClient, section: str, name: str, value: str | None
) -> tuple[str, list[tuple[str, str]]]:
    action, fields = find_form(page(c), section=section)
    return action, replace(fields, name, value)


def test_a_backup_that_no_longer_validates_is_never_restored(client: UiClient, path: Path) -> None:
    submit(client, *replace_form(client, "reading", "vision", "1"))
    (backup,) = config_edit.backups(path)
    backup.write_text("visoin: {}\n", encoding="utf-8")  # valid YAML, not a configuration
    fields = replace(
        restore_form_forged(client, path), "backup", config_edit.pin(backup.read_bytes())
    )
    current = path.read_bytes()
    _, text = submit(client, "/settings/restore", fields)
    assert "The backup cannot be used" in text
    assert path.read_bytes() == current


def restore_form_forged(c: UiClient, path: Path) -> list[tuple[str, str]]:
    csrf = re.search(r'name="csrf" value="([^"]+)"', page(c))
    assert csrf is not None
    return [("csrf", csrf.group(1)), ("pin", config_edit.pin(path.read_bytes()))]


def test_a_forged_post_against_an_invalid_file_is_refused(client: UiClient, path: Path) -> None:
    path.write_text(path.read_text(encoding="utf-8") + "visoin: {}\n", encoding="utf-8")
    fields = [*restore_form_forged(client, path), ("section", "reading"), ("vision", "1")]
    _, text = submit(client, "/settings/save", fields)
    assert "cannot be changed here" in text and "visoin" in text
    assert config_edit.backups(path) == []


def test_the_choice_index_past_the_list_is_400(client: UiClient, path: Path) -> None:
    action, fields = find_form(page(client), section="provider")
    count = len(settings.provider_choices(load_config(path)))
    for value in (str(count), "9999"):
        reply = post(client, action, dict(replace(fields, "choice", value)))
        assert reply.status == 400


def test_a_category_folder_that_cannot_be_read_holds_files(
    client: UiClient, cfg: Any, path: Path
) -> None:
    folder = cfg.output_root / "Images"
    folder.mkdir(parents=True)
    folder.chmod(0)
    try:
        _, text = submit(
            client, *find_form(page(client), section="category-remove", category="Images")
        )
    finally:
        folder.chmod(0o700)
    assert "its folder cannot be read" in text
    assert config_edit.backups(path) == []


# --- B1: the outcome where the form was ------------------------------------------------


def test_the_outcome_is_shown_in_its_section_where_the_browser_lands(client: UiClient) -> None:
    location, text = submit(client, *replace_form(client, "costs", "per_run", "2"))
    assert location.endswith("#o-costs")
    section = text[text.index('id="s-costs"') :]
    assert section.index('id="o-costs"') < section.index("</section>")
    assert text.index("Saved.") > text.index('id="s-costs"')


def test_a_refusal_keeps_what_was_typed(client: UiClient, path: Path) -> None:
    action, fields = find_form(page(client), section="costs")
    _, text = submit(
        client, action, replace(replace(fields, "per_run", "abc"), "per_day", "amount")
    )
    assert "the cap per run must be an amount" in text
    action, fields = find_form(text, section="costs")
    assert dict(fields)["per_run"] == "abc" and dict(fields)["per_day"] == "amount"
    action, fields = find_form(page(client), section="category-add")
    _, text = submit(client, action, replace(replace(fields, "name", ".x"), "hint", "kept hint"))
    assert "must not start with" in text
    assert re.search(r'<details class="other" open><summary>Add a category', text)
    assert dict(find_form(text, section="category-add")[1])["hint"] == "kept hint"
    assert 'value="abc"' not in page(client)  # only on the page right after it


# --- B2: what launchd really has -------------------------------------------------------


def test_a_refused_schedule_says_written_but_not_loaded(
    client: UiClient, path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_LAUNCHD_FAIL", "bootstrap")
    action, fields = next(f for f in forms(page(client)) if f[0] == "/settings/schedule")
    _, text = submit(client, action, replace(fields, "mode", "daily"))
    assert "Written but not loaded, so not running: daily at 12:30" in text
    assert "Loaded and running" not in text


def test_schedule_now_names_what_is_loaded(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    assert settings.schedule_now() == "Nothing is scheduled now."
    scheduler.install_for(path, load_config(path), "07:00", "daily")
    assert settings.schedule_now() == "Loaded and running now: daily at 07:00."


# --- B3: a long page you can move around -----------------------------------------------


def test_the_page_has_a_section_index_and_folds_long_rules(
    client: UiClient, fake_home: Path
) -> None:
    text = page(client)
    for key, title in settings.SECTION_TITLES:
        assert f'<a href="#s-{key}" class="chip">{title}</a>' in text
        assert f'id="s-{key}"' in text
    (fake_home / "Desktop").mkdir()
    submit(client, *find_form(page(client), section="input-add"))
    text = page(client)
    assert '<details class="rules"><summary>Its rules</summary>' in text  # the Desktop's
    assert text.count('<details class="rules">') == 1  # Downloads' rules are short


# --- B4: removing a folder: its comment goes, and a confirmation first -----------------


def test_removing_a_folder_asks_first_and_takes_its_comment(
    serve: list[Ui], path: Path, fake_home: Path
) -> None:
    (fake_home / "Desktop").mkdir()
    text = path.read_text(encoding="utf-8").replace(
        "\nprovider:",
        '  # the Desktop, with rules\n  - path: "~/Desktop"\n    min_age_days: 7\n\nprovider:',
    )
    path.write_text(text, encoding="utf-8")
    c = fresh(serve, path)
    assert 'href="/settings/input-remove?index=1"' in page(c)
    before = path.read_bytes()
    confirm = c.get("/settings/input-remove?index=1")
    assert confirm.status == 200 and "~/Desktop" in confirm.text
    assert_safe(confirm.text)
    assert path.read_bytes() == before  # the confirmation changes nothing
    _, after = submit(c, *find_form(confirm.text, section="input-remove"))
    assert "Saved." in after
    assert "Desktop" not in path.read_text(encoding="utf-8")
    assert "# the fake Downloads" in path.read_text(encoding="utf-8")
    for query in ("", "?index=x", "?index=1&index=0", "?other=1"):
        assert c.get(f"/settings/input-remove{query}").status == 400
    assert "cannot be removed from here" in c.get("/settings/input-remove?index=0").text


def test_a_list_items_own_comment_goes_with_it() -> None:
    text = 'inputs:\n  - "a"  # a\n  # about b\n  # still b\n  - "b"\n\n  # about c\n  - "c"\n'
    after = config_edit.edit_text(text, [config_edit.Delete(("inputs", 1))])
    assert after == 'inputs:\n  - "a"  # a\n\n  # about c\n  - "c"\n'
    after = config_edit.edit_text(text, [config_edit.Delete(("inputs", 2))])
    assert "# about c" not in after and "# about b" in after


# --- B5: the user's backticks are text -------------------------------------------------


def test_backticks_in_a_name_are_never_code(client: UiClient, path: Path) -> None:
    action, fields = find_form(page(client), section="category-add")
    _, text = submit(client, action, replace(fields, "name", "a `rm -rf` b"))
    assert "Category a `rm -rf` b: added" in changed_list(text)
    assert "<code>rm -rf</code>" not in text
    assert settings.prose(settings.lit("x`y`z")) == "x`y`z"


# --- B6: messages ----------------------------------------------------------------------


def test_a_whole_file_refusal_has_no_root_label(downloads: Path) -> None:
    with pytest.raises(ConfigError) as caught:
        parse_config(
            {"inputs": [str(downloads), str(downloads)], "provider": {"name": "fake", "model": "x"}}
        )
    text = settings.invalid(caught.value)
    assert "(root)" not in text and "is listed twice" in text


def test_field_messages_name_the_field(client: UiClient, path: Path) -> None:
    action, fields = find_form(page(client), section="category-add")
    _, text = submit(client, action, replace(fields, "name", "x" * 65))
    assert "the name is longer than 64 characters" in text
    action, fields = find_form(page(client), section="reminders")
    _, text = submit(client, action, replace(fields, "days", None))  # an empty field
    assert "Days before the due date needs a number" in text


def test_restore_warns_that_comments_and_hand_edits_go(client: UiClient) -> None:
    submit(client, *replace_form(client, "reading", "vision", "1"))
    text = client.get("/settings/restore").text
    assert "comments and changes made by hand since that backup are lost" in text


# --- the language, every pair (the reviewer's property test) ---------------------------


PAIRS = [(a, b) for a in LOCALES for b in LOCALES if a != b]


@pytest.mark.parametrize("ocr", ["", "ocr:\n", "ocr:\n  enabled: true\n"])
@pytest.mark.parametrize(("old", "new"), PAIRS)
def test_a_language_switch_changes_nothing_else(
    fake_home: Path, old: str, new: str, ocr: str
) -> None:
    target = fake_home / "c.yaml"
    target.write_text(
        f"language: {old}\nprovider:\n  name: fake\n  model: x\n{ocr}", encoding="utf-8"
    )
    before = load_config(target)
    snap = settings.read(target)
    assert snap.raw is not None and snap.config is not None
    ctx = settings.Context(snap.raw, snap.config, {"language": [new]}, target)
    saved = settings.commit(snap, snap.raw, settings._language_edits(ctx), before)
    after = load_config(target)
    assert after == saved.config
    a, b = before.model_dump(), after.model_dump()
    assert a.pop("language") == old and b.pop("language") == new
    assert a == b


def test_a_run_in_progress_keeps_the_section(client: UiClient, cfg: Any) -> None:
    with instance_lock(cfg.database):
        location, text = submit(client, *replace_form(client, "reading", "vision", "1"))
    assert location.endswith("#o-reading") and "going on now" in text


def test_editing_one_category_leaves_the_others_lines_alone(serve: list[Ui], path: Path) -> None:
    append(path, CATS.replace("  - name: Work\n", "  # my work papers\n  - name: Work  # keep\n"))
    c = fresh(serve, path)
    action, fields = find_form(page(c), section="category-edit", category="Bills")
    submit(c, action, replace(fields, "layout", "year"))
    text = path.read_text(encoding="utf-8")
    assert "  # my work papers\n  - name: Work  # keep\n" in text
    assert "    hint: |-\n      Utility bills.\n      Not receipts.\n" in text


def test_a_backups_sequence_number_is_read(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    name = "config.yaml.backup-20260329-013000Z"
    assert config_edit.stamp_of(target, tmp_path / f"{name}-11") == ("20260329-013000", 11)
    assert config_edit.stamp_of(target, tmp_path / name) == ("20260329-013000", 1)
    assert config_edit.stamp_of(target, tmp_path / f"{name}-1") is None  # two digits at least


def test_an_untouched_daily_cap_keeps_its_digits(serve: list[Ui], path: Path) -> None:
    text = path.read_text(encoding="utf-8").replace(
        "  max_cost_usd_per_run: 1.00",
        "  max_cost_usd_per_run: 1.00\n  max_cost_usd_per_day: 0.00001",
    )
    path.write_text(text, encoding="utf-8")
    c = fresh(serve, path)
    action, fields = find_form(page(c), section="costs")
    assert dict(fields)["day_amount"] == "0.00001"
    _, after = submit(c, action, replace(fields, "per_run", "2"))
    assert "Saved." in after
    assert data(path)["limits"] == {"max_cost_usd_per_run": 2.0, "max_cost_usd_per_day": 0.00001}
