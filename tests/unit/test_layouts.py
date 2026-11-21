"""Category layouts (#86): validation, issuer folders and where the planner puts files."""

from __future__ import annotations

import os
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from file_librarian import planner
from file_librarian.config import Category, ConfigError, layout_segments
from file_librarian.models import Classification, Decision, FileContext, FileStatus
from file_librarian.naming import issuer_folder_name, issuer_key, issuer_matches, match_issuer
from file_librarian.planner import Placement, layout_parts, plan
from file_librarian.privacy import scrub_answer
from file_librarian.providers.prompt import clean_issuer, normalize_answer
from tests._helpers import answer, fake_config

TODAY = date(2026, 11, 20)


def layout_config(tmp_path: Path, layout: str | None, **kw: Any) -> Any:
    downloads = tmp_path / "Downloads"
    downloads.mkdir(exist_ok=True)
    categories = [{"name": "Finance", "layout": layout}, {"name": "Other"}]
    return fake_config(downloads, categories=categories, **kw)


def decision(path: Path, reply: Classification, *, text: str | None = None) -> Decision:
    ctx = FileContext(
        path=path, sha256=path.name.ljust(64, "0"), size_bytes=1, extension=path.suffix, text=text
    )
    return Decision(ctx, reply)


# --- configuration --------------------------------------------------------------------


@pytest.mark.parametrize(
    "layout",
    ["{year}", "{year}/{month}", "{issuer}", "{issuer}/{year}", "{year}-{month}", "Y{year}"],
)
def test_valid_layouts(tmp_path: Path, layout: str) -> None:
    cfg = layout_config(tmp_path, layout)
    category = cfg.category("Finance")
    assert category is not None and category.layout == layout
    assert layout_segments(layout) == tuple(layout.split("/"))


def test_no_layout_is_the_default(tmp_path: Path) -> None:
    assert Category(name="Finance").layout is None
    assert fake_config(tmp_path).categories[0].layout is None


@pytest.mark.parametrize(
    ("layout", "message"),
    [
        ("{day}", "unknown placeholder"),
        ("{}", "unknown placeholder"),
        ("{year:04}", "no format spec"),
        ("{year!r}", "no format spec"),
        ("/{year}", "must not start or end with '/'"),
        ("{year}/", "must not start or end with '/'"),
        ("{year}//{month}", "must not start or end with '/'"),
        ("{year}/{month}/{issuer}/{year}", "at most 3 levels"),
        ("{year}\\{month}", "separated by '/' only"),
        ("{year}:{month}", "separated by '/' only"),
        ("../{year}", "not a folder name"),
        ("./{year}", "not a folder name"),
        (".{year}", "must not start with '.'"),
        (" {year}", "must not start or end with spaces"),
        ("Y\x01{year}", "control or invisible"),
        ("Bills {issuer}", "{issuer} must be a whole sub-folder"),
        ("Archive", "at least one placeholder"),
        ("{year", "not a valid pattern"),
        ("x" * 70 + "{year}", "longer than 64"),
    ],
)
def test_invalid_layouts_are_refused_at_load(tmp_path: Path, layout: str, message: str) -> None:
    with pytest.raises(ConfigError, match=r"categories\.0\.layout") as info:
        layout_config(tmp_path, layout)
    assert message in str(info.value)


# --- issuer names ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "spelling",
    ["Acme", "ACME", "acme", "Acme Inc.", "ACME, Inc", "Acme Ltda", "Acme S.A.", "Acme S/A",
     "The Acme Corporation", "Ácme", unicodedata.normalize("NFD", "Ácme"), "Acme L.L.C.",
     "Acme GmbH", "Acme Ltd.", "Acme EIRELI", "acme.com", "Acme.com.br", "Acme Inc. Ltd"],
)  # fmt: skip
def test_spellings_of_one_company_share_a_key(spelling: str) -> None:
    assert issuer_key(spelling) == "acme"
    assert match_issuer(spelling, ["Other Corp", "Acme"]) == "Acme"


def test_a_legal_form_alone_is_kept() -> None:
    assert issuer_key("Inc") == "inc"
    assert issuer_key("S.A.") == "sa"


@pytest.mark.parametrize(
    "name", ["Power Co", "Power Company", "Lotus Spa", "Pay Me", "Fiat Spa", "Acme & Co"]
)
def test_ordinary_words_are_never_taken_for_a_legal_form(name: str) -> None:
    # "Co", "Company", "Spa" and "Me" can be part of a name.
    assert issuer_key(name) != issuer_key(name.split()[0])


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Banco Inter SA", "banco inter"),
        ("Banco Inter S A", "banco inter"),
        ("Banco Inter, SA.", "banco inter"),
        ("SA", "sa"),  # a name of its own
        ("Fiat SpA", "fiat"),
        ("Fiat S.p.A.", "fiat"),
        ("Visa", "visa"),
    ],
)
def test_a_trailing_sa_or_spa_is_a_legal_form(name: str, key: str) -> None:
    assert issuer_key(name) == key
    if key != "sa":
        assert issuer_key("Lotus Spa") != issuer_key("Lotus")


@pytest.mark.parametrize(
    ("new", "existing"),
    [
        ("PowerCo", "Power Co"),
        ("POWER CO.", "Power Co"),
        ("Fabrikkam", "Fabrikam"),
        ("Nortwhind", "Northwind"),
        ("Banco do Brasil S.A.", "Banco do Brasil"),
        ("Amazon", "Amazon.com"),
        ("Café", unicodedata.normalize("NFD", "Café Ltda")),
        ("Acme Partners", "Acme:Partners"),  # Finder shows it as "Acme/Partners"
    ],
)
def test_similar_spellings_join_the_folder_already_there(new: str, existing: str) -> None:
    assert match_issuer(new, ["Contoso", existing]) == existing


@pytest.mark.parametrize(
    ("new", "existing"),
    [
        ("Acne", "Acme"),  # one letter changed: another name
        ("Acmes", "Acme"),  # too short to guess
        ("Contoso 2", "Contoso 3"),  # numbers tell companies apart
        ("Tailspin Toys", "Tailspin Tots"),
        ("Acme Bank", "Acme"),  # another word: maybe another company
        ("Northwind Bank", "Northwind Brands"),
        ("Fabrikam", "Fabrikan"),
        ("Woodgrove", "Woodgrave"),
        ("Morgans", "Morgan"),  # a plural is another name
        ("Williams", "William"),
        ("Siemens", "Siemen"),
        ("Northwind Traders", "Northwind Trader"),
        ("Lotus Spa", "Lotus"),
        ("Pay Me", "Pay"),
        ("Power Company", "Power Co"),
        ("Studio 5455", "Studio 545"),  # a doubled digit
        ("Studio 54", "Studio 45"),  # swapped digits
        ("Contoso 12", "Contoso 21"),
    ],
)
def test_near_miss_names_are_never_merged(new: str, existing: str) -> None:
    assert match_issuer(new, [existing]) is None


def test_two_equally_close_folders_are_not_guessed_between() -> None:
    assert match_issuer("Northwind", ["Nortwhind", "Northwnid"]) is None


def test_every_folder_of_the_best_tier_is_returned() -> None:
    assert issuer_matches("Acme", ["ACME Corp", "Acme", "PowerCo"]) == ["ACME Corp", "Acme"]
    assert issuer_matches("Power Co", ["PowerCo", "Contoso"]) == ["PowerCo"]
    assert issuer_matches("...", ["Acme"]) == []


def test_an_issuer_without_letters_matches_nothing() -> None:
    assert match_issuer("...", ["Acme"]) is None
    assert issuer_folder_name("...") is None
    assert issuer_folder_name(" / ") is None


def test_issuer_folder_names_are_safe() -> None:
    assert issuer_folder_name("Acme Inc.") == "Acme Inc"
    assert issuer_folder_name("Acme... ") == "Acme"
    nfd = issuer_folder_name(unicodedata.normalize("NFD", "Café"))
    assert nfd == unicodedata.normalize("NFC", "Café")
    assert issuer_folder_name("Power/Co") == "Power-Co"
    assert issuer_folder_name("  ..Acme  ") == "Acme"
    assert issuer_folder_name("A\x00cme") == "A-cme"
    long = issuer_folder_name("Acme " * 30)
    assert long is not None and len(long) <= 64 and long == long.strip()


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Acme", "Acme"),
        ("  Power   Co ", "Power Co"),
        ("", None),
        ("null", None),
        ("Unknown", None),
        (None, None),
        (42, None),
        ("Acme (+55 11 91234-5678)", None),
        ("contact@example.com", None),
        ("x" * 150, "x" * 100),
    ],
)
def test_the_answer_issuer_is_cleaned(raw: object, clean: str | None) -> None:
    assert clean_issuer(raw) == clean
    data = normalize_answer({"category": "Finance", "issuer": raw})
    assert data["issuer"] == clean


def test_scrub_drops_an_issuer_holding_an_identifier() -> None:
    reply = answer("Finance", issuer="Acme 123.456.789-09")
    assert scrub_answer(reply).issuer is None
    kept = answer("Finance", issuer="Acme")
    assert scrub_answer(kept).issuer == "Acme"


# --- the planner -----------------------------------------------------------------------


def category(layout: str) -> Category:
    return Category(name="Finance", layout=layout)


def test_year_and_month_come_from_the_date(tmp_path: Path) -> None:
    placement = Placement()
    parts = layout_parts(
        category("{year}/{month}"), tmp_path, date(2026, 3, 9), None, placement.folders
    )
    assert parts == ("2026", "03")
    parts = layout_parts(
        category("{year}-{month}"), tmp_path, date(987, 12, 1), None, Placement().folders
    )
    assert parts == ("0987-12",)


def test_unknown_issuer_stops_at_the_deepest_known_folder(tmp_path: Path) -> None:
    folders = Placement().folders
    assert layout_parts(category("{year}/{issuer}"), tmp_path, TODAY, None, folders) == ("2026",)
    assert layout_parts(category("{issuer}/{year}"), tmp_path, TODAY, None, folders) == ()
    assert layout_parts(category("{issuer}/{year}"), tmp_path, TODAY, "...", folders) == ()


def test_something_that_is_not_a_folder_stops_the_layout(tmp_path: Path) -> None:
    (tmp_path / "2026").write_text("a file named like the folder")
    (tmp_path / "real").mkdir()
    (tmp_path / "Acme").symlink_to(tmp_path / "real")
    folders = Placement().folders
    assert layout_parts(category("{year}"), tmp_path, TODAY, None, folders) == ()
    # A symlink is never a candidate, nor used: the issuer stops there.
    assert layout_parts(category("{issuer}"), tmp_path, TODAY, "Acme", folders) == ()


def test_a_folder_only_in_icloud_stops_the_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "2026").mkdir()
    monkeypatch.setattr(planner, "is_dataless", lambda _st: True)
    assert layout_parts(category("{year}"), tmp_path, TODAY, None, Placement().folders) == ()
    # A folder not there yet is made, iCloud or not.
    assert layout_parts(
        category("{year}"), tmp_path, date(2025, 1, 1), None, Placement().folders
    ) == ("2025",)


def test_issuer_folders_already_on_disk_are_reused_with_their_spelling(tmp_path: Path) -> None:
    (tmp_path / "ACME Inc").mkdir()
    (tmp_path / ".hidden").mkdir()
    folders = Placement().folders
    assert layout_parts(category("{issuer}"), tmp_path, TODAY, "acme", folders) == ("ACME Inc",)
    assert layout_parts(category("{issuer}"), tmp_path, TODAY, "Hidden", folders) == ("Hidden",)
    # A folder planned earlier in the same plan counts like one on disk.
    assert layout_parts(category("{issuer}"), tmp_path, TODAY, "HIDDEN, Inc.", folders) == (
        "Hidden",
    )


def test_new_files_land_in_their_layout_folders(tmp_path: Path) -> None:
    cfg = layout_config(tmp_path, "{year}/{issuer}")
    lib = cfg.output_root
    inbox = tmp_path / "Downloads"
    decisions = [
        decision(inbox / "a.pdf", answer("Finance", document_date=date(2024, 5, 1), issuer="Acme")),
        decision(inbox / "b.pdf", answer("Finance", issuer="ACME, Inc.")),
        decision(inbox / "c.pdf", answer("Finance")),
        decision(inbox / "d.pdf", answer("Other", issuer="Acme")),
        decision(inbox / "e.pdf", answer("Finance", "x", 0.2, issuer="Acme")),
    ]
    planned = plan(decisions, cfg, today=TODAY)
    where = {i.context.path.name: i.destination for i in planned.items}
    assert where == {
        "a.pdf": lib / "Finance" / "2024" / "Acme" / "a.pdf",
        # Another year: its folder is spelled like the one of 2024.
        "b.pdf": lib / "Finance" / "2026" / "Acme" / "b.pdf",
        "c.pdf": lib / "Finance" / "2026" / "c.pdf",  # no issuer: the year folder
        "d.pdf": lib / "Other" / "d.pdf",  # no layout
        "e.pdf": lib / cfg.review_folder / "e.pdf",  # review never gets a layout
    }


def test_the_same_company_lands_in_one_folder_within_a_plan(tmp_path: Path) -> None:
    cfg = layout_config(tmp_path, "{issuer}")
    inbox = tmp_path / "Downloads"
    names = ["Power Co", "POWER CO", "PowerCo", "Power Co."]
    decisions = [
        decision(inbox / f"{i}.pdf", answer("Finance", issuer=name)) for i, name in enumerate(names)
    ]
    planned = plan(decisions, cfg, today=TODAY)
    assert {i.destination.parent.name for i in planned.items if i.destination} == {"Power Co"}


def test_the_download_date_comes_before_the_processing_date(tmp_path: Path) -> None:
    cfg = layout_config(tmp_path, "{year}")
    ctx = FileContext(
        path=tmp_path / "Downloads" / "a.pdf",
        sha256="a" * 64,
        size_bytes=1,
        extension=".pdf",
        downloaded=date(2023, 1, 2),
    )
    planned = plan([Decision(ctx, answer("Finance"))], cfg, today=TODAY)
    assert planned.items[0].destination == cfg.output_root / "Finance" / "2023" / "a.pdf"
    assert planned.items[0].layout_date == date(2023, 1, 2)
    # A file already filed keeps the date the index has, over any other.
    kept = {ctx.path: date(2022, 7, 7)}
    reply = answer("Finance", document_date=date(2021, 1, 1))
    planned = plan([Decision(ctx, reply)], cfg, today=TODAY, layout_dates=kept)
    assert planned.items[0].destination == cfg.output_root / "Finance" / "2022" / "a.pdf"
    assert planned.items[0].layout_date == date(2022, 7, 7)


def test_names_collide_per_final_folder(tmp_path: Path) -> None:
    cfg = layout_config(tmp_path, "{year}")
    lib = cfg.output_root
    (lib / "Finance" / "2026").mkdir(parents=True)
    (lib / "Finance" / "2026" / "bill.pdf").write_text("already there")
    (lib / "Finance" / "bill.pdf").write_text("at the top, not in the way")
    one, two = tmp_path / "Downloads" / "bill.pdf", tmp_path / "Downloads" / "x" / "bill.pdf"
    old = date(2025, 1, 1)
    decisions = [
        decision(one, answer("Finance")),
        decision(two, answer("Finance")),
        decision(tmp_path / "Downloads" / "y" / "bill.pdf", answer("Finance", document_date=old)),
    ]
    planned = plan(decisions, cfg, today=TODAY)
    assert [i.destination for i in planned.items] == [
        lib / "Finance" / "2026" / "bill-2.pdf",
        lib / "Finance" / "2026" / "bill-3.pdf",
        lib / "Finance" / "2025" / "bill.pdf",
    ]
    assert all(i.status is FileStatus.ORGANIZED for i in planned.items)


@pytest.mark.parametrize("part", ["", ".", "..", "a/b", "a\x00b", "new:folder"])
def test_destination_refuses_a_sub_folder_that_is_not_plain(tmp_path: Path, part: str) -> None:
    cfg = layout_config(tmp_path, "{year}")
    ctx = FileContext(
        path=tmp_path / "Downloads" / "a.pdf", sha256="a" * 64, size_bytes=1, extension=""
    )
    with pytest.raises(planner.UnsafeDestinationError, match="not a plain folder name"):
        planner._destination(cfg, "Finance", "a.pdf", planner._Names(), ctx, (part,))


def test_destination_takes_sub_folders_only_in_a_category(tmp_path: Path) -> None:
    cfg = layout_config(tmp_path, "{year}")
    ctx = FileContext(
        path=tmp_path / "Downloads" / "a.pdf", sha256="a" * 64, size_bytes=1, extension=""
    )
    with pytest.raises(planner.UnsafeDestinationError, match="has no layout"):
        planner._destination(cfg, cfg.review_folder, "a.pdf", planner._Names(), ctx, ("2026",))
    deep = ("2026", "05", "Acme")
    final = planner._destination(cfg, "Finance", "a.pdf", planner._Names(), ctx, deep)
    assert final == cfg.output_root / "Finance" / "2026" / "05" / "Acme" / "a.pdf"
    # A folder already on disk is taken as it is spelled there.
    (cfg.output_root / "Finance" / "Acme:Partners").mkdir(parents=True)
    taken = planner._destination(cfg, "Finance", "a.pdf", planner._Names(), ctx, ("Acme:Partners",))
    assert taken.parent.name == "Acme:Partners"


# --- review round 2: tie-breaks and folder identity -----------------------------------


def test_same_folder_compares_names_when_a_folder_does_not_exist(tmp_path: Path) -> None:
    assert planner.same_folder(tmp_path / "Acme" / "x", tmp_path / "ACME" / "x")
    nfd = unicodedata.normalize("NFD", "Café")
    assert planner.same_folder(tmp_path / nfd, tmp_path / "Café")
    assert not planner.same_folder(tmp_path / "Acme", tmp_path / "Acne")
    (tmp_path / "real").mkdir()
    assert planner.same_folder(tmp_path / "real", tmp_path / "REAL")


def test_tie_break_the_folder_the_file_is_in(tmp_path: Path) -> None:
    for name in ("ACME Corp", "Acme"):
        (tmp_path / name).mkdir()
    folders = planner._Folders(made_by_flib=[tmp_path / "ACME Corp"])
    own = tmp_path / "Acme" / "a.pdf"
    assert folders.issuer(tmp_path, "Acme Inc.", own=own) == "Acme"


def test_tie_break_the_folder_flib_made(tmp_path: Path) -> None:
    for name in ("ACME Corp", "Acme"):
        (tmp_path / name).mkdir()
    os.utime(tmp_path / "Acme", (1e9, 1e9))  # older too, so flib's must win on its own
    folders = planner._Folders(made_by_flib=[tmp_path / "ACME Corp"])
    assert folders.issuer(tmp_path, "Acme") == "ACME Corp"


def test_tie_break_the_oldest_folder(tmp_path: Path) -> None:
    for name in ("ACME Corp", "Acme"):
        (tmp_path / name).mkdir()
    os.utime(tmp_path / "Acme", (1e9, 1e9))  # on APFS this moves the birth time back too
    assert planner._Folders().issuer(tmp_path, "acme") == "Acme"


def test_siblings_are_only_layout_folders_and_lend_only_the_same_name(tmp_path: Path) -> None:
    (tmp_path / "Taxes" / "Fabrikkam").mkdir(parents=True)  # the user's own folder
    (tmp_path / "2024" / "Fabrikkam").mkdir(parents=True)  # a misspelling in a year folder
    (tmp_path / "2023" / "ACME Inc").mkdir(parents=True)
    cat = Category(name="Finance", layout="{year}/{issuer}")
    folders = planner._Folders()
    assert folders.level(tmp_path, ["{year}"]) == [tmp_path / "2023", tmp_path / "2024"]
    when = date(2026, 1, 1)
    # A slip is never borrowed from another year; the same name is.
    assert layout_parts(cat, tmp_path, when, "Fabrikam", folders) == ("2026", "Fabrikam")
    assert layout_parts(cat, tmp_path, when, "Acme", folders) == ("2026", "ACME Inc")
