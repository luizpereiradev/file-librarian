"""``flib ui``'s Settings page (#117): the essentials, changed from the browser.

Each change is driven through a real server on 127.0.0.1 as a browser sends it
(a form post with the session cookie, the exact origin and the CSRF token), on
a commented configuration file in a temporary folder. Every test checks the
file on disk, parsed again: the change is there, the comments and the other
keys are not touched, a backup holds the old bytes, and a refused change
writes nothing at all.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
import yaml

from file_librarian import config_edit, keychain, scheduler
from file_librarian.config import Config, load_config
from file_librarian.lock import instance_lock
from file_librarian.ui import server as ui_server
from file_librarian.ui import settings
from file_librarian.ui.server import Ui
from tests._helpers import UiClient, UiResponse, answer
from tests.unit.test_ui import (  # noqa: F401 - fixtures: every server is checked the same way
    _Collector,
    events,
    no_hang,
    nothing_left_behind,
)
from tests.unit.test_ui_pages import assert_safe, fill, opened

SECRET = "sk-ant-api03-SECRET-never-on-a-page-0123456789"  # noqa: S105 - a fake key
EVIL = "<img src=x onerror=alert(1)> {{csrf}}"

FILE = """\
# file-librarian configuration, written for this test.
language: en  # names in English

# Folders to keep organized.
inputs:
  - {downloads}  # the fake Downloads

provider:
  name: fake  # offline
  model: x

limits:
  max_cost_usd_per_run: 1.00  # estimated

vision:
  enabled: false  # images stay here

schedule:
  time: "12:30"  # the daily run
"""


@pytest.fixture
def serve(loopback_network: None) -> Iterator[list[Ui]]:
    started: list[Ui] = []
    yield started
    for ui in started:
        ui.close()


@pytest.fixture
def path(fake_home: Path, downloads: Path) -> Path:
    folder = fake_home / ".config" / "file-librarian"
    folder.mkdir(parents=True)
    target = folder / "config.yaml"
    target.write_text(FILE.format(downloads=json.dumps(str(downloads))), encoding="utf-8")
    target.chmod(0o600)
    return target


@pytest.fixture
def cfg(path: Path) -> Config:  # the file's configuration, for the server and the index
    return load_config(path)


def start(serve: list[Ui], cfg: Config, path: Path | None) -> UiClient:
    ui = Ui(cfg, poll_seconds=0.01, config_path=path)
    ui.start()
    serve.append(ui)
    c = UiClient(ui.port, ui.host_name)
    assert c.launch(ui.launch_url).status == 303
    return c


@pytest.fixture
def client(serve: list[Ui], cfg: Config, path: Path) -> UiClient:
    return start(serve, cfg, path)


def page(c: UiClient) -> str:
    reply = c.get("/settings")
    assert reply.status == 200
    assert_safe(reply.text)
    return reply.text


def form_values(c: UiClient) -> tuple[str, str]:
    text = page(c)
    csrf = re.search(r'name="csrf" value="([^"]+)"', text)
    pin = re.search(r'name="pin" value="([0-9a-f]{20})"', text)
    assert csrf is not None and pin is not None
    return csrf.group(1), pin.group(1)


def post(c: UiClient, route: str, fields: Mapping[str, object], **kw: str) -> UiResponse:
    from urllib.parse import urlencode

    return c.request(
        "POST",
        route,
        body=urlencode(fields, doseq=True).encode(),
        headers={
            "Origin": c.origin,
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        | kw,
    )


def change(
    c: UiClient,
    section: str,
    route: str = "/settings/save",
    pin: str | None = None,
    **fields: str | list[str],
) -> str:
    """Post one section's form as the page does, follow the redirect; the page's text."""
    csrf, shown_pin = form_values(c)
    body: dict[str, str | list[str]] = {"csrf": csrf, "pin": pin or shown_pin}
    body |= fields
    if route == "/settings/save":
        body["section"] = section
    reply = post(c, route, body)
    assert reply.status == 303, (reply.status, reply.text)
    location = reply.headers["Location"]
    assert re.fullmatch(r"/settings\?n=[A-Za-z0-9_-]{16}(#o-[a-z]+)?", location), location
    result = c.get(location.split("#", 1)[0])
    assert result.status == 200
    assert_safe(result.text)
    return result.text


def on_disk(path: Path) -> dict[str, object]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    load_config(path)  # and still valid
    return data


def comments(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if "#" in line]


def unchanged(path: Path, before: bytes) -> None:
    assert path.read_bytes() == before
    assert config_edit.backups(path) == []


READING = {"ocr": "1"}  # the reading form as shown: OCR on, the rest off


# --- the page --------------------------------------------------------------------------


def test_the_page_shows_every_section_and_the_menu_links_it(client: UiClient) -> None:
    text = page(client)
    for title in (
        "When to organize",
        "Who classifies",
        "What flib reads and sends",
        "Folders",
        "Categories",
        "Cost caps",
        "Notifications and what is due",
        "Language",
        "Everything else",
    ):
        assert f">{title}</h2>" in text, title
    assert 'href="/settings" aria-current="page"' in text
    assert "coming soon" not in text
    assert "~/.config/file-librarian/config.yaml" in text
    assert "<code>flib doctor</code>" in text
    for path in ("/status", "/review", "/search", "/activity", "/due"):
        assert '<a href="/settings">Settings</a>' in client.get(path).text, path


def test_flib_ui_can_open_on_settings() -> None:
    assert "settings" in ui_server.PAGES


def test_every_form_carries_the_pin_and_the_csrf_token(client: UiClient, path: Path) -> None:
    text = page(client)
    forms = re.findall(r'<form [^>]*action="(/settings/[a-z]+)"[^>]*>(.*?)</form>', text, re.S)
    assert len(forms) > 10
    pin = config_edit.pin(path.read_bytes())
    for action, body in forms:
        assert 'name="csrf"' in body, action
        if action != "/settings/open":
            assert f'name="pin" value="{pin}"' in body, action


def test_every_label_is_tied_to_its_control(client: UiClient) -> None:
    text = page(client)
    for control in re.findall(r"<(?:input|select)\b[^>]*>", text):
        if 'type="hidden"' in control:
            continue
        name = re.search(r'name="([^"]+)"', control)
        assert name is not None, control
        before = text[: text.index(control)]
        assert before.rfind("<label") > before.rfind("</label>"), control  # inside a label


def test_settings_need_the_session(client: UiClient) -> None:
    for where in ("/settings", "/settings/restore"):
        assert UiClient(client.port, client.name).get(where).status == 403


def test_the_address_takes_nothing_else(client: UiClient) -> None:
    for query in ("?x=1", "?n=a&n=b", "?path=/etc"):
        assert client.get(f"/settings{query}").status == 400
    assert client.get("/settings/restore?x=1").status == 400


def test_an_outcome_no_longer_kept_says_so(client: UiClient) -> None:
    assert "no longer kept" in client.get("/settings?n=AAAAAAAAAAAAAAAA").text


def test_without_its_file_the_page_only_reads(serve: list[Ui], cfg: Config, path: Path) -> None:
    c = start(serve, cfg, None)
    text = page(c)
    assert "does not know its configuration file" in text and "settings-form" not in text
    csrf = re.search(r'name="csrf" value="([^"]+)"', text)
    assert csrf is not None
    before = path.read_bytes()
    reply = post(
        c, "/settings/save", {"csrf": csrf.group(1), "section": "reading", "pin": "0" * 20}
    )
    assert reply.status == 303
    assert "does not know its configuration file" in c.get(reply.headers["Location"]).text
    assert "does not know" in c.get("/settings/restore").text
    reply = post(c, "/settings/open", {"csrf": csrf.group(1)})
    assert "does not know" in c.get(reply.headers["Location"].split("#")[0]).text
    unchanged(path, before)


def test_an_invalid_file_is_shown_with_the_field_and_why(client: UiClient, path: Path) -> None:
    path.write_text(path.read_text(encoding="utf-8").replace('"12:30"', '"25:99"'), "utf-8")
    text = page(client)
    assert "cannot be used" in text
    assert "schedule.time" in text and "valid 24-hour time" in text
    assert "settings-form" not in text  # nothing to change but the file itself
    path.write_text("a: [1\n", encoding="utf-8")
    assert "not valid YAML (line 2" in page(client)
    path.write_bytes(b"\xff\xfe")
    assert "not UTF-8" in page(client)
    path.unlink()
    assert "There is no configuration file there" in page(client)


# --- each setting, end to end ----------------------------------------------------------


def test_vision_on_is_saved_with_comments_kept_and_a_backup(
    client: UiClient, path: Path, serve: list[Ui]
) -> None:
    before = path.read_bytes()
    kept = comments(path)
    text = change(client, "reading", vision="1", ocr="1")
    assert "Saved." in text and "Images to the model (vision)" in text and "off → on" in text
    assert "Restore the previous settings" in text
    assert on_disk(path)["vision"] == {"enabled": True}
    assert "  enabled: true  # images stay here" in path.read_text(encoding="utf-8")
    assert [c for c in kept if "images stay here" not in c] == [
        c for c in comments(path) if "images stay here" not in c
    ]
    (backup,) = config_edit.backups(path)
    assert backup.read_bytes() == before
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 == stat.S_IMODE(backup.stat().st_mode)
    assert serve[0].config.vision.enabled  # the server goes on with what it saved


def test_ocr_and_search_by_meaning(client: UiClient, path: Path) -> None:
    change(client, "reading", semantic="1")
    data = on_disk(path)
    assert data["ocr"] == {"enabled": False}
    assert data["search"] == {"semantic": {"enabled": True}}
    assert data["vision"] == {"enabled": False}


def test_saving_what_is_already_there_writes_nothing(client: UiClient, path: Path) -> None:
    before = path.read_bytes()
    assert settings.NOTHING in change(client, "reading", **READING)
    unchanged(path, before)


def test_the_provider_is_a_fixed_choice(client: UiClient, path: Path) -> None:
    text = page(client)
    labels = re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', text)
    offered = dict(labels)
    assert offered["0"] == "The test provider (nothing is sent): x"
    anthropic = next(k for k, v in offered.items() if v.startswith("Anthropic API"))
    assert "Ollama, on this Mac: gemma4:e4b" in offered.values()
    assert not any("OpenAI" in v for v in offered.values())  # the file names no server
    assert 'name="base_url"' not in text and 'type="url"' not in text
    text = change(client, "provider", choice=anthropic)
    assert "Who classifies" in text and "fake → anthropic" in text
    data = on_disk(path)
    assert data["provider"] == {"name": "anthropic", "model": "claude-haiku-5-5"}
    assert '  name: "anthropic"  # offline' in path.read_text(encoding="utf-8")
    assert "Anthropic API key: not set" in page(client)


def test_a_provider_choice_the_page_never_offered_is_refused(client: UiClient, path: Path) -> None:
    before = path.read_bytes()
    csrf, pin = form_values(client)
    for value in ("99", "-1", "anthropic", "", "1e3", "\u0661"):  # the last: Arabic-Indic 1
        fields = {"csrf": csrf, "pin": pin, "section": "provider", "choice": value}
        assert post(client, "/settings/save", fields).status == 400, value
    unchanged(path, before)


def test_ollama_is_offered_only_on_this_mac_and_a_server_only_as_the_file_has_it(
    tmp_path: Path, downloads: Path
) -> None:
    from file_librarian.config import parse_config

    remote = parse_config(
        {
            "inputs": [str(downloads)],
            "provider": {
                "name": "openai-compat",
                "model": "m",
                "base_url": "https://api.example.com/v1/route-SECRET",
            },
        }
    )
    labels = [c.label for c in settings.provider_choices(remote)]
    assert labels[0] == "OpenAI-compatible API at api.example.com: m"
    assert not any("Ollama" in label for label in labels)
    assert "route-SECRET" not in " ".join(labels) + settings.leaves(remote)
    assert settings.leaves(remote).endswith("go to api.example.com.")
    local = parse_config(
        {
            "inputs": [str(downloads)],
            "provider": {"name": "ollama", "model": "m", "base_url": "http://127.0.0.1:11434/v1"},
        }
    )
    assert settings.leaves(local) == "Nothing leaves this Mac: the model runs here."
    # Another server is never suggested with a model the page made up.
    assert [c.model for c in settings.provider_choices(remote) if c.name == "openai-compat"] == [
        "m"
    ]
    anthropic = parse_config(
        {
            "inputs": [str(downloads)],
            "provider": {"name": "anthropic", "model": "m", "base_url": "https://h.example/v1"},
        }
    )
    assert not any(c.name == "openai-compat" for c in settings.provider_choices(anthropic))


def test_cost_caps(client: UiClient, path: Path) -> None:
    change(client, "costs", per_run="2.5", per_day="amount", day_amount="4")
    assert on_disk(path)["limits"] == {"max_cost_usd_per_run": 2.5, "max_cost_usd_per_day": 4.0}
    assert "  max_cost_usd_per_run: 2.5  # estimated" in path.read_text(encoding="utf-8")
    change(client, "costs", per_run="", per_day="none")
    assert on_disk(path)["limits"] == {"max_cost_usd_per_run": None, "max_cost_usd_per_day": None}
    assert load_config(path).limits.cost_cap_per_day is None
    change(client, "costs", per_run="1", per_day="default")
    assert on_disk(path)["limits"] == {"max_cost_usd_per_run": 1.0}
    assert load_config(path).limits.cost_cap_per_day == 3.0
    assert "Three times the cap per run" in page(client)


@pytest.mark.parametrize(
    ("fields", "says"),
    [
        ({"per_run": "abc", "per_day": "default"}, "the cap per run must be an amount"),
        ({"per_run": "0", "per_day": "default"}, "the cap per run must be an amount"),
        ({"per_run": "-1", "per_day": "default"}, "the cap per run must be an amount"),
        ({"per_run": "5000", "per_day": "default"}, "the cap per run must be an amount"),
        ({"per_run": "1", "per_day": "amount", "day_amount": ""}, "the daily cap must be"),
        ({"per_run": "1", "per_day": "amount", "day_amount": "1e3"}, "the daily cap must be"),
    ],
)
def test_a_bad_amount_is_said_and_nothing_is_written(
    client: UiClient, path: Path, fields: dict[str, str], says: str
) -> None:
    before = path.read_bytes()
    text = change(client, "costs", **fields)
    assert "Not saved" in text and says in text
    unchanged(path, before)


def test_reminders_and_the_due_list(client: UiClient, path: Path) -> None:
    change(client, "reminders", remind="1", days="5", kinds=["bill", "tax"])
    data = on_disk(path)
    assert data["summary"] == {"notify": False}
    assert data["due"] == {"remind_days_before": 5, "kinds": ["bill", "tax"]}
    text = change(client, "reminders", notify="1", days="5", kinds=[])
    assert on_disk(path)["due"] == {"remind_days_before": 5, "kinds": [], "remind": False}
    assert "Kinds on the Due list" in text


@pytest.mark.parametrize("days", ["61", "-1", "x", "100", "\u0663"])
def test_bad_days_are_said(client: UiClient, path: Path, days: str) -> None:
    before = path.read_bytes()
    text = change(client, "reminders", days=days)
    assert "from 0 to 60" in text
    unchanged(path, before)


def test_a_kind_the_page_never_offered_is_refused(client: UiClient, path: Path) -> None:
    before = path.read_bytes()
    csrf, pin = form_values(client)
    for kinds in (["bill", "bill"], ["password"]):
        fields = {"csrf": csrf, "pin": pin, "section": "reminders", "days": "3", "kinds": kinds}
        assert post(client, "/settings/save", fields).status == 400
    unchanged(path, before)


def test_duplicates(client: UiClient, path: Path) -> None:
    change(client, "duplicates", duplicates="leave")
    assert on_disk(path)["duplicates"] == "leave"


def test_the_language_never_moves_the_library(client: UiClient, path: Path) -> None:
    old = load_config(path)
    text = change(client, "language", language="pt-BR")
    assert "Language" in text and "en → pt-BR" in text
    new = load_config(path)
    assert new.language == "pt-BR"
    assert new.output_root == old.output_root and new.review_folder == old.review_folder
    assert [c.name for c in new.categories] == [c.name for c in old.categories]
    assert new.ocr.languages == old.ocr.languages
    assert new.duplicates_folder == old.duplicates_folder
    assert "# names in English" in path.read_text(encoding="utf-8")  # its comment stays


# --- categories ------------------------------------------------------------------------


def test_a_category_is_added_with_its_hint_and_layout(client: UiClient, path: Path) -> None:
    old = [c.name for c in load_config(path).categories]
    change(client, "category-add", name="Taxes", hint="tax returns", layout="year")
    categories = on_disk(path)["categories"]
    assert isinstance(categories, list)
    assert [c["name"] for c in categories] == [*old, "Taxes"]  # the defaults, written out
    assert categories[-1] == {
        "name": "Taxes",
        "hint": "tax returns",
        "rename": False,
        "layout": "{year}",
    }
    change(client, "category-add", name="Manuals", rename="1", layout="none")
    assert on_disk(path)["categories"][-1] == {"name": "Manuals"}  # type: ignore[index]


@pytest.mark.parametrize(
    ("name", "says"),
    [
        (".hidden", "must not start with &#x27;.&#x27;"),
        ("a/b", "plain folder name"),
        ("Finance", "same folder as"),
        ("finance", "same folder as"),
        ("", "a category needs a name"),
        ("x‮", "invisible characters"),
    ],
)
def test_a_bad_category_name_is_said_with_its_field(
    client: UiClient, path: Path, name: str, says: str
) -> None:
    before = path.read_bytes()
    text = change(client, "category-add", name=name, layout="none")
    assert says in text, text
    unchanged(path, before)


def test_a_long_or_multiline_hint_is_refused(client: UiClient, path: Path) -> None:
    before = path.read_bytes()
    assert "the hint is longer than 300" in change(
        client, "category-add", name="A", hint="x" * 301, layout="none"
    )
    assert "the hint has invisible characters" in change(
        client, "category-add", name="A", hint="a\u0007b", layout="none"
    )
    unchanged(path, before)


def test_a_category_is_edited(client: UiClient, path: Path) -> None:
    change(
        client,
        "category-edit",
        hint_field="1",
        category="Images",
        name="Pictures",
        hint="photos",
        layout="year-month",
    )
    categories = load_config(path).categories
    pictures = next(c for c in categories if c.name == "Pictures")
    assert pictures.hint == "photos" and pictures.layout == "{year}/{month}"
    assert not pictures.rename and all(c.name != "Images" for c in categories)
    change(
        client,
        "category-edit",
        hint_field="1",
        category="Pictures",
        name="Pictures",
        hint="",
        layout="none",
        rename="1",
    )
    written = on_disk(path)["categories"]
    assert isinstance(written, list)
    raw = next(c for c in written if c["name"] == "Pictures")
    assert raw == {"name": "Pictures", "rename": True}


def test_a_category_that_holds_files_is_renamed_only_by_reprocess(
    client: UiClient, cfg: Config, path: Path, downloads: Path
) -> None:
    fill(downloads, cfg, {"bill.pdf": answer("Finance", confidence=0.95)})
    before = path.read_bytes()
    text = change(
        client,
        "category-edit",
        hint_field="1",
        category="Finance",
        name="Money",
        layout="none",
        rename="1",
    )
    assert "was not renamed" in text and "the index has 1 file in it" in text
    assert "<code>flib reprocess --rename-category Finance Money</code>" in text
    text = change(client, "category-remove", category="Finance")
    assert "was not removed" in text and "--remove-category" in text
    unchanged(path, before)
    # Its hint can still change: nothing moves for that.
    change(
        client,
        "category-edit",
        hint_field="1",
        category="Finance",
        name="Finance",
        hint="money",
        layout="none",
        rename="1",
    )
    assert load_config(path).category("Finance").hint == "money"  # type: ignore[union-attr]


def test_a_category_whose_folder_has_files_is_not_renamed(
    client: UiClient, cfg: Config, path: Path
) -> None:
    folder = cfg.output_root / "Images"
    folder.mkdir(parents=True)
    (folder / ".DS_Store").write_bytes(b"")
    change(
        client,
        "category-edit",
        hint_field="1",
        category="Images",
        name="Pics",
        layout="none",
        rename="1",
    )
    assert load_config(path).category("Pics") is not None  # only .DS_Store: empty
    folder = cfg.output_root / "Pics"
    folder.mkdir()
    (folder / "photo.png").write_bytes(b"x")
    before = path.read_bytes()
    text = change(client, "category-remove", category="Pics")
    assert "its folder is not empty" in text
    assert path.read_bytes() == before


def test_an_empty_category_is_removed(client: UiClient, path: Path) -> None:
    change(client, "category-remove", category="Images")
    assert load_config(path).category("Images") is None


def test_a_category_an_input_files_into_is_kept(
    serve: list[Ui], fake_home: Path, downloads: Path
) -> None:
    target = fake_home / "c.yaml"
    target.write_text(
        f"inputs:\n  - path: {json.dumps(str(downloads))}\n    category: Images\n"
        "provider: {name: fake, model: x}\n",
        encoding="utf-8",
    )
    c = start(serve, load_config(target), target)
    text = change(c, "category-remove", category="Images")
    assert "files everything in it" in text
    assert config_edit.backups(target) == []


def test_an_unknown_category_is_stale_and_a_bad_layout_is_refused(
    client: UiClient, path: Path
) -> None:
    before = path.read_bytes()
    assert "changed since" in change(client, "category-remove", category="Nope")
    csrf, pin = form_values(client)
    fields = {
        "csrf": csrf,
        "pin": pin,
        "section": "category-add",
        "name": "A",
        "layout": "{year}/../x",
    }
    assert post(client, "/settings/save", fields).status == 400
    unchanged(path, before)


def test_hostile_category_names_are_text(serve: list[Ui], fake_home: Path, downloads: Path) -> None:
    target = fake_home / "c.yaml"
    target.write_text(
        f"inputs: [{json.dumps(str(downloads))}]\nprovider: {{name: fake, model: x}}\n"
        f"categories:\n  - name: {json.dumps(EVIL)}\n    hint: {json.dumps(EVIL)}\n"
        "  - name: Other\n",
        encoding="utf-8",
    )
    c = start(serve, load_config(target), target)
    text = page(c)
    assert "&lt;img src=x onerror=alert(1)&gt; {{csrf}}" in text
    change(
        c,
        "category-edit",
        hint_field="1",
        category=EVIL,
        name=EVIL,
        hint="calm",
        layout="none",
        rename="1",
    )
    assert load_config(target).category(EVIL).hint == "calm"  # type: ignore[union-attr]


# --- folders ---------------------------------------------------------------------------


def test_the_desktop_is_added_with_inits_rules_and_removed(
    client: UiClient, path: Path, fake_home: Path
) -> None:
    (fake_home / "Desktop").mkdir()
    text = page(client)
    assert "Organize Desktop too" in text and "Organize Downloads too" not in text
    change(client, "input-add", folder="desktop")
    inputs = on_disk(path)["inputs"]
    assert isinstance(inputs, list) and inputs[1]["path"] == "~/Desktop"
    config = load_config(path)
    assert config.inputs[1].min_age_days == 7 and config.inputs[1].folders == "ignore"
    assert "# the fake Downloads" in path.read_text(encoding="utf-8")
    assert "Stop organizing" in page(client)
    change(client, "input-remove", index="1")
    assert load_config(path).input_paths == [config.input_paths[0]]
    assert "Stop organizing" not in page(client)  # never the last one


def test_the_last_input_cannot_be_removed(client: UiClient, path: Path) -> None:
    before = path.read_bytes()
    assert "at least one input folder" in change(client, "input-remove", index="0")
    csrf, pin = form_values(client)
    for index in ("1", "x", "-1"):
        fields = {"csrf": csrf, "pin": pin, "section": "input-remove", "index": index}
        assert post(client, "/settings/save", fields).status == 400
    unchanged(path, before)


def test_a_folder_is_never_taken_from_the_form(client: UiClient, path: Path) -> None:
    before = path.read_bytes()
    csrf, pin = form_values(client)
    for folder in ("/", "~", "../../etc", "/Users", "documents", ""):
        fields = {"csrf": csrf, "pin": pin, "section": "input-add", "folder": folder, "path": "/"}
        assert post(client, "/settings/save", fields).status == 400, folder
    unchanged(path, before)


def test_a_desktop_that_is_a_link_is_refused(
    client: UiClient, path: Path, fake_home: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (fake_home / "Desktop").symlink_to(elsewhere)
    assert "Desktop: cannot be added, it is a link" in page(client)
    before = path.read_bytes()
    assert "was not added: it is a link" in change(client, "input-add", folder="desktop")
    unchanged(path, before)


def test_what_may_be_an_input(
    cfg: Config, fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = fake_home / "Library" / "Mail"
    library.mkdir(parents=True)
    hidden = fake_home / ".secret"
    hidden.mkdir()
    afile = fake_home / "notes.txt"
    afile.write_text("x", encoding="utf-8")
    cfg.output_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    cases = {
        Path("/"): "too broad",
        fake_home: "too broad",
        library: "a system folder",
        hidden: "hidden",
        afile: "not a folder",
        fake_home / "missing": "no such folder",
        cfg.output_root: "the library",
        cfg.input_paths[0]: "an input folder already",
        outside: "outside your home folder",
        Path("relative"): "not a plain folder path",
        Path(f"{fake_home}/a/../b"): "not a plain folder path",
    }
    for folder, says in cases.items():
        why = settings.folder_problem(folder, cfg)
        assert why is not None and says in why, (folder, why)
    good = fake_home / "Desktop"
    good.mkdir()
    assert settings.folder_problem(good, cfg) is None
    monkeypatch.setattr(os, "getuid", lambda: 12345)
    assert settings.folder_problem(good, cfg) == "it is not your folder"


# --- the schedule ----------------------------------------------------------------------


def loaded(state: Path) -> dict[str, str]:
    return json.loads(state.read_text()) if state.exists() else {}


def test_a_daily_schedule_is_installed_and_written(
    client: UiClient, path: Path, fake_launchd: Path
) -> None:
    text = change(client, "schedule", route="/settings/schedule", mode="daily", time="09:15")
    assert "Daily run scheduled at 09:15" in text
    assert "<code>flib doctor</code> checks" in text
    assert on_disk(path)["schedule"] == {"time": "09:15", "mode": "daily"}
    assert '  time: "09:15"  # the daily run' in path.read_text(encoding="utf-8")
    assert scheduler.LABEL in loaded(fake_launchd)
    assert scheduler.status().time == "09:15"
    assert scheduler.scheduled_config() == path.resolve()
    assert 'value="daily" checked' in page(client)


def test_watch_mode_and_off(client: UiClient, path: Path, fake_launchd: Path) -> None:
    text = change(client, "schedule", route="/settings/schedule", mode="watch", time="")
    assert "Watch mode on" in text
    assert set(loaded(fake_launchd)) == {scheduler.WATCH_LABEL}
    assert on_disk(path)["schedule"] == {"time": "12:30", "mode": "watch"}
    change(client, "schedule", route="/settings/schedule", mode="both", time="07:00")
    assert set(loaded(fake_launchd)) == {scheduler.LABEL, scheduler.WATCH_LABEL}
    before = path.read_bytes()
    text = change(client, "schedule", route="/settings/schedule", mode="off", time="07:00")
    assert "The schedule is off" in text
    assert loaded(fake_launchd) == {} and not scheduler.status().installed
    assert path.read_bytes() == before  # off is launchd only, as `flib schedule --remove`
    assert settings.NOTHING in change(client, "schedule", route="/settings/schedule", mode="off")


def test_adding_an_input_refreshes_watch_mode(
    client: UiClient, path: Path, fake_home: Path
) -> None:
    change(client, "schedule", route="/settings/schedule", mode="watch", time="12:30")
    (fake_home / "Desktop").mkdir()
    text = change(client, "input-add", folder="desktop")
    assert "Watch mode" in text and "now watches these folders" in text
    assert str(fake_home / "Desktop") in scheduler.status().watch_paths


def test_launchd_refusing_is_said_honestly(
    client: UiClient, path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_LAUNCHD_FAIL", "bootstrap")
    text = change(client, "schedule", route="/settings/schedule", mode="daily", time="08:00")
    assert "launchd did not take the schedule (the settings were saved)" in text
    assert "<code>flib schedule</code> in Terminal says why" in text
    assert "Restore the previous settings" in text
    assert on_disk(path)["schedule"] == {"time": "08:00", "mode": "daily"}


@pytest.mark.parametrize("fields", [{"mode": "hourly"}, {"mode": "daily", "time": "x"}])
def test_a_bad_schedule_is_refused(client: UiClient, path: Path, fields: dict[str, str]) -> None:
    before = path.read_bytes()
    csrf, pin = form_values(client)
    reply = post(client, "/settings/schedule", {"csrf": csrf, "pin": pin} | fields)
    if fields["mode"] == "hourly":
        assert reply.status == 400
    else:
        assert "24-hour time" in client.get(reply.headers["Location"]).text
    unchanged(path, before)
    assert not scheduler.status().installed


def test_the_schedule_warns_about_a_key_only_in_the_environment(
    client: UiClient, path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = page(client)
    anthropic = next(
        k for k, v in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)<', text) if "Anthropic" in v
    )
    change(client, "provider", choice=anthropic)
    text = change(client, "schedule", route="/settings/schedule", mode="daily", time="09:00")
    assert "No API key is set" in text
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    text = change(client, "schedule", route="/settings/schedule", mode="daily", time="09:01")
    assert "only in this Terminal" in text and SECRET not in text


# --- what never happens ----------------------------------------------------------------


def test_a_file_changed_since_the_page_is_never_overwritten(client: UiClient, path: Path) -> None:
    csrf, pin = form_values(client)
    hand = path.read_text(encoding="utf-8") + "# edited by hand\n"
    path.write_text(hand, encoding="utf-8")
    fields = {"csrf": csrf, "pin": pin, "section": "reading", "vision": "1", "ocr": "1"}
    reply = post(client, "/settings/save", fields)
    assert reply.status == 303
    text = client.get(reply.headers["Location"]).text
    assert "changed since this page was shown" in text and "Nothing was saved" in text
    assert path.read_text(encoding="utf-8") == hand
    assert config_edit.backups(path) == []
    assert config_edit.pin(hand.encode()) in text  # the page now has the new state


def test_two_tabs_the_second_is_refused(client: UiClient, path: Path) -> None:
    csrf, pin = form_values(client)
    first = {"csrf": csrf, "pin": pin, "section": "reading", "vision": "1", "ocr": "1"}
    second = {"csrf": csrf, "pin": pin, "section": "duplicates", "duplicates": "leave"}
    assert post(client, "/settings/save", first).status == 303
    reply = post(client, "/settings/save", second)
    assert "changed since" in client.get(reply.headers["Location"]).text
    data = on_disk(path)
    assert data["vision"] == {"enabled": True} and "duplicates" not in data
    assert len(config_edit.backups(path)) == 1


def test_a_run_in_progress_holds_every_change(client: UiClient, cfg: Config, path: Path) -> None:
    before = path.read_bytes()
    with instance_lock(cfg.database):
        text = change(client, "reading", vision="1", ocr="1")
        assert "A flib run, or another change, is going on now" in text
        text = change(client, "schedule", route="/settings/schedule", mode="daily", time="09:00")
        assert "going on now" in text
    unchanged(path, before)
    assert not scheduler.status().installed


def test_reload_back_and_forward_never_change_again(client: UiClient, path: Path) -> None:
    csrf, pin = form_values(client)
    fields = {"csrf": csrf, "pin": pin, "section": "reading", "vision": "1", "ocr": "1"}
    location = post(client, "/settings/save", fields).headers["Location"]
    after = path.read_bytes()
    for _ in range(3):
        assert client.get(location.split("#")[0]).status == 200
        assert client.get("/settings").status == 200
    assert path.read_bytes() == after and len(config_edit.backups(path)) == 1


def test_getting_every_page_changes_nothing(
    client: UiClient, path: Path, fake_launchd: Path
) -> None:
    folder = path.parent
    before = sorted((p.name, p.read_bytes()) for p in folder.iterdir())
    for where in ("/settings", "/settings/restore", "/settings?n=AAAAAAAAAAAAAAAA"):
        assert client.get(where).status == 200
    assert sorted((p.name, p.read_bytes()) for p in folder.iterdir()) == before
    assert loaded(fake_launchd) == {}


def test_a_forged_post_changes_nothing(client: UiClient, path: Path) -> None:
    before = path.read_bytes()
    csrf, pin = form_values(client)
    good = {"csrf": csrf, "pin": pin, "section": "reading", "vision": "1"}
    for route in ("/settings/save", "/settings/schedule", "/settings/restore", "/settings/open"):
        assert post(client, route, good, Origin="http://evil.example").status == 403
        assert post(client, route, good, **{"Sec-Fetch-Site": "same-site"}).status == 403
        assert post(client, route, good | {"csrf": "x"}).status == 403
        assert post(UiClient(client.port, client.name), route, good).status == 403
    unchanged(path, before)
    for route in ("/settings/save", "/settings/schedule", "/settings/restore", "/settings/open"):
        assert route in ui_server.POST_ROUTES


@pytest.mark.parametrize(
    "fields",
    [
        {"section": "nope"},
        {"section": "reading", "pin": "short"},
        {"section": "reading", "vision": "yes"},
        {"section": "reading", "vision": ["1", "1"]},
        {"section": "duplicates", "duplicates": "delete"},
        {"section": "language", "language": "xx"},
        {"section": "costs", "per_day": "sometimes"},
    ],
)
def test_a_form_the_page_never_sends_is_400(
    client: UiClient, path: Path, fields: dict[str, object]
) -> None:
    before = path.read_bytes()
    csrf, pin = form_values(client)
    body = {"csrf": csrf, "pin": pin} | fields
    assert post(client, "/settings/save", body).status == 400
    unchanged(path, before)


def test_a_linked_file_is_never_replaced(
    serve: list[Ui], cfg: Config, path: Path, tmp_path: Path
) -> None:
    link = tmp_path / "linked.yaml"
    link.symlink_to(path)
    c = start(serve, cfg, link)
    before = path.read_bytes()
    text = change(c, "reading", vision="1", ocr="1")
    assert "symbolic link" in text and "Not saved" in text
    assert link.is_symlink() and path.read_bytes() == before


def test_an_unknown_key_is_named_and_nothing_can_be_changed(
    serve: list[Ui], cfg: Config, fake_home: Path, downloads: Path
) -> None:
    target = fake_home / "c.yaml"
    target.write_text(
        f"inputs: [{json.dumps(str(downloads))}]\nprovider: {{name: fake, model: x}}\n"
        "visoin: {enabled: true}\n",
        encoding="utf-8",
    )
    c = start(serve, cfg, target)
    text = page(c)
    assert "cannot be used" in text and "visoin" in text and "settings-form" not in text


def test_aliases_are_refused_not_half_done(
    serve: list[Ui], fake_home: Path, downloads: Path
) -> None:
    target = fake_home / "c.yaml"
    target.write_text(
        f"inputs: [{json.dumps(str(downloads))}]\nprovider: {{name: fake, model: x}}\n"
        "vision: &v {enabled: false}\nocr: {enabled: true}\nsearch: {semantic: *v}\n",
        encoding="utf-8",
    )
    c = start(serve, load_config(target), target)
    before = target.read_bytes()
    text = change(c, "reading", vision="1", ocr="1")
    assert "aliases" in text and "Change it in the configuration file instead" in text
    assert target.read_bytes() == before


def test_another_database_is_refused(
    serve: list[Ui], cfg: Config, path: Path, fake_home: Path
) -> None:
    c = start(serve, cfg, path)
    path.write_text(
        path.read_text(encoding="utf-8") + f"database: {json.dumps(str(fake_home / 'other.db'))}\n",
        "utf-8",
    )
    before = path.read_bytes()
    assert "another database" in change(c, "reading", vision="1", ocr="1")
    assert path.read_bytes() == before


# --- secrets ---------------------------------------------------------------------------


def test_the_api_key_never_reaches_any_byte(
    serve: list[Ui], fake_home: Path, downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keychain.write(SECRET, keychain.ACCOUNT)
    keychain.write(SECRET + "-compat", keychain.OPENAI_COMPAT_ACCOUNT)
    keychain.write(SECRET + "-embed", keychain.EMBEDDINGS_ACCOUNT)
    monkeypatch.setenv("FLIB_EMBEDDINGS_API_KEY", SECRET + "-env")
    env_file = fake_home / ".env"
    env_file.write_text(f"ANTHROPIC_API_KEY={SECRET}-file\n", encoding="utf-8")
    env_file.chmod(0o600)
    target = fake_home / "c.yaml"
    target.write_text(
        f"inputs: [{json.dumps(str(downloads))}]\n"
        f"provider:\n  name: anthropic\n  model: m\n  env_file: {json.dumps(str(env_file))}\n"
        "  base_url: https://api.example.com/v1/path-SECRET\n"
        "search:\n  semantic:\n    base_url: https://embed.example.com/v1/embed-SECRET\n",
        encoding="utf-8",
    )
    c = start(serve, load_config(target), target)
    replies = [c.get("/settings"), c.get("/settings/restore")]
    csrf, pin = form_values(c)
    for fields in (
        {"section": "reading", "vision": "1", "ocr": "1", "semantic": "1"},
        {"section": "costs", "per_run": "zz", "per_day": "default"},
    ):
        reply = post(
            c,
            "/settings/save",
            {"csrf": csrf, "pin": config_edit.pin(target.read_bytes())} | fields,
        )
        replies += [reply, c.get(reply.headers["Location"].split("#")[0])]
    replies += [
        c.get("/settings/restore"),
        c.get("/settings?x=1"),
        post(c, "/settings/save", {"csrf": csrf}),
    ]
    replies += [
        post(c, "/settings/schedule", {"csrf": csrf, "pin": pin, "mode": "daily", "time": "09:00"})
    ]
    replies += [c.get("/settings"), c.get("/status"), c.get("/api/v1/status")]
    for reply in replies:
        raw = bytes(reply.body) + str(reply.headers).encode()
        assert SECRET.encode() not in raw
        assert b"path-SECRET" not in raw and b"embed-SECRET" not in raw
    text = c.get("/settings").text
    assert "Anthropic API key: set (in the Keychain)" in text
    assert "embed.example.com" in text


def test_a_key_is_said_set_or_not_and_never_shown(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from file_librarian.config import parse_config

    provider = parse_config(
        {"inputs": [str(downloads)], "provider": {"name": "anthropic", "model": "m"}}
    ).provider
    assert settings.key_state(provider) == (
        "Anthropic API key: not set. `flib key set` in Terminal saves it in the Keychain."
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    assert settings.key_state(provider) == "Anthropic API key: set (in the environment)."
    fake = parse_config({"inputs": [str(downloads)], "provider": {"name": "fake", "model": "m"}})
    assert settings.key_state(fake.provider) is None


def test_addresses_show_their_host_only() -> None:
    assert (
        settings.host_of("https://user@api.example.com:8443/v1/abc?k=SECRET")
        == "api.example.com:8443"
    )
    assert settings.host_of("http://[::1") == "an address in the file"
    assert settings.describe("provider.base_url", "https://h.example/x-SECRET") == "h.example"


# --- restore ---------------------------------------------------------------------------


def test_restore_the_previous_settings(client: UiClient, path: Path) -> None:
    original = path.read_bytes()
    change(client, "reading", vision="1", ocr="1")
    changed = path.read_bytes()
    confirm = client.get("/settings/restore")
    assert confirm.status == 200
    assert_safe(confirm.text)
    assert "Images to the model (vision)" in confirm.text and "on → off" in confirm.text
    assert path.read_bytes() == changed  # the confirmation changes nothing
    found = dict(re.findall(r'name="(pin|backup)" value="([0-9a-f]+)"', confirm.text))
    csrf = re.search(r'name="csrf" value="([^"]+)"', confirm.text)
    assert csrf is not None
    reply = post(client, "/settings/restore", {"csrf": csrf.group(1)} | found)
    assert reply.status == 303
    text = client.get(reply.headers["Location"]).text
    assert "The previous settings are back" in text
    assert path.read_bytes() == original
    assert config_edit.backups(path)[0].read_bytes() == changed  # undoable the same way
    # The same confirmation posted again: the file changed since, refused.
    reply = post(client, "/settings/restore", {"csrf": csrf.group(1)} | found)
    assert "changed since" in client.get(reply.headers["Location"]).text
    assert path.read_bytes() == original


def test_restore_refuses_a_backup_that_changed_or_is_invalid(client: UiClient, path: Path) -> None:
    assert "no backup to restore" in client.get("/settings/restore").text
    change(client, "reading", vision="1", ocr="1")
    confirm = client.get("/settings/restore").text
    found = dict(re.findall(r'name="(pin|backup)" value="([0-9a-f]+)"', confirm))
    csrf = re.search(r'name="csrf" value="([^"]+)"', confirm)
    assert csrf is not None
    (backup,) = config_edit.backups(path)
    backup.write_text("a: [1\n", encoding="utf-8")
    assert "latest backup cannot be used" in client.get("/settings/restore").text
    current = path.read_bytes()
    reply = post(client, "/settings/restore", {"csrf": csrf.group(1)} | found)
    assert "backup changed since" in client.get(reply.headers["Location"]).text
    assert path.read_bytes() == current
    for bad in ({"backup": "x"}, {"pin": "y"}):
        assert (
            post(client, "/settings/restore", {"csrf": csrf.group(1)} | found | bad).status == 400
        )


# --- open in an editor -----------------------------------------------------------------


def test_open_in_a_text_editor_opens_the_servers_own_path(
    client: UiClient, path: Path, fake_browser: Path
) -> None:
    csrf, _ = form_values(client)
    reply = post(client, "/settings/open", {"csrf": csrf, "path": "/etc/passwd"})
    assert reply.status == 303 and reply.headers["Location"].endswith("#o-file")
    text = client.get(reply.headers["Location"].split("#")[0]).text
    assert "Opened in your text editor" in text
    assert text.index("Opened in your text editor") > text.index('id="s-file"')
    calls = opened(fake_browser)
    assert calls[-1][-4:] == ["-t", "--", str(path)] or calls[-1][-3:] == ["-t", "--", str(path)]


def test_when_the_editor_cannot_open_it_the_page_says_so(
    client: UiClient, path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_OPEN_FAIL", "1")
    csrf, _ = form_values(client)

    def opened_page() -> str:
        reply = post(client, "/settings/open", {"csrf": csrf})
        assert reply.status == 303
        return client.get(reply.headers["Location"].split("#")[0]).text

    assert "macOS could not open it" in opened_page()
    path.unlink()
    path.mkdir()
    assert "not a plain file" in opened_page()
    path.rmdir()
    assert "no configuration file there" in opened_page()


def test_the_last_category_is_never_removed(
    serve: list[Ui], fake_home: Path, downloads: Path
) -> None:
    target = fake_home / "c.yaml"
    target.write_text(
        f"inputs: [{json.dumps(str(downloads))}]\nprovider: {{name: fake, model: x}}\n"
        "categories:\n  - name: Only\n",
        encoding="utf-8",
    )
    c = start(serve, load_config(target), target)
    assert "at least one category" in change(c, "category-remove", category="Only")
    assert config_edit.backups(target) == []


def test_restore_refuses_a_backup_naming_another_database(
    client: UiClient, path: Path, fake_home: Path
) -> None:
    change(client, "reading", vision="1", ocr="1")
    (backup,) = config_edit.backups(path)
    other = f"database: {json.dumps(str(fake_home / 'other.db'))}\n"
    backup.write_text(backup.read_text(encoding="utf-8") + other, encoding="utf-8")
    confirm = client.get("/settings/restore").text
    found = dict(re.findall(r'name="(pin|backup)" value="([0-9a-f]+)"', confirm))
    csrf, _ = form_values(client)
    current = path.read_bytes()
    reply = post(client, "/settings/restore", {"csrf": csrf} | found)
    assert "another database" in client.get(reply.headers["Location"]).text
    assert path.read_bytes() == current
