"""``flib ui`` in a real browser (#114): a smoke test over Chrome's DevTools protocol.

Opt-in: ``FLIB_TEST_BROWSER=1 uv run pytest -m browser`` (CI runs it on macOS, whose
runner image has Chrome). It finds an installed Chrome or Chromium
(``FLIB_TEST_BROWSER_PATH`` names another), runs it headless with a throwaway
profile, and drives it like a person: the launch through the handoff file, a search
submitted by a real click (post, redirect, get; Back and Forward), the CSV export,
and End session, whose page must still get its stylesheet. Every form post goes
through the server's real origin checks, which is what unit tests with a hand-made
``Origin`` header could not show: a browser sends ``Origin: null`` under some
referrer policies. Nothing goes beyond 127.0.0.1.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from file_librarian import pipeline
from file_librarian.config import Config
from file_librarian.db.store import Store
from file_librarian.providers.fake import FakeProvider
from file_librarian.ui.server import Ui
from tests._cdp import find_browser, headless
from tests._helpers import answer, put
from tests.unit.test_ui import nothing_left_behind  # noqa: F401 - no socket left behind

BROWSER = find_browser()

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("FLIB_TEST_BROWSER") != "1" or BROWSER is None,
        reason="the real-browser test is opt-in: FLIB_TEST_BROWSER=1 and Chrome or Chromium",
    ),
]


def test_a_session_in_a_real_browser(
    loopback_network: None, downloads: Path, cfg: Config, tmp_path: Path
) -> None:
    assert BROWSER is not None
    put(downloads, "power bill.pdf", b"power")
    put(downloads, "water bill.pdf", b"water")
    pipeline.run(
        cfg,
        FakeProvider(
            {
                "power bill.pdf": answer(
                    "Finance", description="Power", amount="10", currency="USD"
                ),
                "water bill.pdf": answer("Finance", description="Water"),
            }
        ),
        simulate=False,
    )
    ui = Ui(cfg, poll_seconds=0.05)
    ui.start()
    profile = tmp_path / "profile"
    downloads_to = tmp_path / "downloaded"
    downloads_to.mkdir()
    try:
        handoff = ui.write_handoff()
        with headless(BROWSER, profile) as (tab, browser):
            browser(
                "Browser.setDownloadBehavior",
                {"behavior": "allow", "downloadPath": str(downloads_to)},
            )
            # The launch: the browser opens the handoff file, which posts the token.
            tab.call("Page.navigate", {"url": handoff.as_uri()})
            tab.wait_for(lambda: tab.url() == f"{ui.origin}/status" and tab.ready())
            assert "Status" in tab.text()
            # A search, submitted by a click: a POST that must pass the origin checks.
            tab.goto(f"{ui.origin}/search")
            tab.evaluate("document.querySelector('input[name=q]').value = 'power'")
            tab.click("form.search button[type=submit]:not([formaction])")
            tab.wait_for(lambda: "/search?r=" in tab.url() and tab.ready())
            results = tab.url()
            assert "power" not in results  # the words stay out of the address
            assert "power bill.pdf" in tab.text() and "1 match" in tab.text()
            # Back and Forward: plain GETs, no "Confirm Form Resubmission".
            tab.evaluate("history.back()")
            tab.wait_for(lambda: tab.url().endswith("/search") and tab.ready())
            tab.evaluate("history.forward()")
            tab.wait_for(lambda: tab.url() == results and tab.ready())
            assert "power bill.pdf" in tab.text()
            # The export, from the results page.
            tab.click("button[formaction='/search/export']")
            csv = downloads_to / "flib-search.csv"
            tab.wait_for(lambda: csv.exists())
            assert "power bill.pdf" in csv.read_text(encoding="utf-8")
            # End session: the page says so, styled although the server is ending.
            tab.click("form.end button")
            tab.wait_for(lambda: "This session has ended" in tab.text() and tab.ready())
            tab.wait_for(
                lambda: bool(
                    tab.evaluate(
                        "getComputedStyle(document.body).backgroundColor !== 'rgba(0, 0, 0, 0)'"
                    )
                )
            )
        assert ui.wait(10) == "ended"
    finally:
        ui.close()
        shutil.rmtree(profile, ignore_errors=True)


def test_review_and_undo_in_a_real_browser(
    loopback_network: None, downloads: Path, cfg: Config, tmp_path: Path
) -> None:
    """Accept a file by a real click (#115): it moves on disk; Back and Forward are
    plain GETs that do nothing again; Undo, confirmed by a click, puts it back."""
    assert BROWSER is not None
    put(downloads, "shot.png", b"pixels")
    put(downloads, "other.png", b"more pixels")
    unsure = answer("Images", confidence=0.3)
    pipeline.run(cfg, FakeProvider({"shot.png": unsure, "other.png": unsure}), simulate=False)
    in_review = cfg.output_root / cfg.review_folder / "shot.png"
    filed = cfg.output_root / "Images" / "shot.png"
    assert in_review.exists()
    with Store.open(cfg.database) as store:
        shot = store.file_by_current_path(in_review).id  # type: ignore[union-attr]
    ui = Ui(cfg, poll_seconds=0.05)
    ui.start()
    profile = tmp_path / "profile"
    try:
        handoff = ui.write_handoff()
        with headless(BROWSER, profile) as (tab, _):
            tab.call("Page.navigate", {"url": handoff.as_uri()})
            tab.wait_for(lambda: tab.url() == f"{ui.origin}/status" and tab.ready())
            tab.goto(f"{ui.origin}/review")
            assert "2 files in review" in tab.text()
            # Accept, by a click on the first file's button: post, redirect, get.
            tab.click(f"form[action='/review/file']:has(input[name=id][value='{shot}']) button")
            tab.wait_for(lambda: "/review?n=" in tab.url() and tab.ready())
            after = tab.url()
            assert "is now in Images." in tab.text()
            assert filed.read_bytes() == b"pixels" and not in_review.exists()
            # Back and Forward: no resubmission, nothing moves again.
            tab.evaluate("history.back()")
            tab.wait_for(lambda: tab.url() == f"{ui.origin}/review" and tab.ready())
            # (the page may come back as it was shown; its forms carry the pin, so a
            # second click on that old Accept would be refused, not repeated)
            assert "in review" in tab.text() and filed.read_bytes() == b"pixels"
            tab.evaluate("history.forward()")
            tab.wait_for(lambda: tab.url() == after and tab.ready())
            assert filed.read_bytes() == b"pixels"
            # Undo: the confirmation page (a GET), then a click.
            tab.click(".outcome a")
            tab.wait_for(lambda: "/undo?run=" in tab.url() and tab.ready())
            assert "shot.png" in tab.text() and filed.exists()  # nothing undone yet
            tab.click("form.confirm button")
            tab.wait_for(lambda: "/undo?n=" in tab.url() and tab.ready())
            assert "1 restored" in tab.text()
            assert in_review.read_bytes() == b"pixels" and not filed.exists()
            tab.evaluate("location.reload()")
            tab.wait_for(lambda: "/undo?n=" in tab.url() and tab.ready())
            assert in_review.exists() and not filed.exists()
            # At 390 px no page scrolls sideways, and every menu item is in view.
            tab.call(
                "Emulation.setDeviceMetricsOverride",
                {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True},
            )
            for page in ("status", "review", "search", "activity", "due", f"undo?file={shot}"):
                tab.goto(f"{ui.origin}/{page}")
                # A phone widens its layout to fit what overflows: measure against 390.
                wide = tab.evaluate(
                    "Math.max(window.innerWidth, document.documentElement.scrollWidth) - 390"
                )
                assert wide <= 0, (page, wide)
                hidden = tab.evaluate(
                    "[...document.querySelectorAll('nav[aria-label=Pages] > *')]"
                    ".filter(e => e.getBoundingClientRect().right > 390).length"
                )
                assert hidden == 0, (page, hidden)
    finally:
        ui.close()
        shutil.rmtree(profile, ignore_errors=True)
