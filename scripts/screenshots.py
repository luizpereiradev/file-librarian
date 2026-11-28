"""The site's screenshots, taken from the demo library in a headless browser.

    uv run python -m scripts.screenshots

For each language it builds the demo library (``scripts/demo.py``) in a new
temporary folder, serves ``flib ui`` from it on loopback, and drives a headless
Chrome or Chromium with a throwaway profile (``tests/_cdp.py``) through every
page: 1440 pixels wide, light, plus the Review page dark. It also renders the menu
bar item's menu from the demo's state, with the real pose icon and the menu's
own words. Everything is written to ``docs/assets/screenshots/<language>/`` as
WebP, and the temporary folders are removed.

No window opens, nothing is installed, and nothing but 127.0.0.1 is reached. The
pages show only the demo's fictional files.
"""

from __future__ import annotations

import base64
import html
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from tests._cdp import Tab, find_browser, headless

from scripts import demo

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "assets" / "screenshots"
WIDTH = 1440
MAX_HEIGHT = 2000
QUALITY = 80
PAGES = ("status", "review", "search", "activity", "due", "settings")
QUERY = {"en": "invoice", "pt-BR": "fatura"}


def size(tab: Tab, height: int) -> None:
    tab.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": WIDTH, "height": height, "deviceScaleFactor": 1, "mobile": False},
    )


def capture(tab: Tab, path: Path) -> None:
    """The whole page, up to ``MAX_HEIGHT`` pixels."""
    height = int(tab.evaluate("Math.ceil(document.documentElement.scrollHeight)"))
    size(tab, max(600, min(height, MAX_HEIGHT)))
    params: dict[str, Any] = {"format": "webp", "quality": QUALITY}
    data = tab.call("Page.captureScreenshot", params)["data"]
    path.write_bytes(base64.b64decode(data))
    size(tab, 900)
    print(f"  {path.relative_to(REPO)}")


def dark(tab: Tab, on: bool) -> None:
    value = "dark" if on else "light"
    tab.call(
        "Emulation.setEmulatedMedia",
        {"features": [{"name": "prefers-color-scheme", "value": value}]},
    )


def menu_html(built: demo.Demo) -> str:
    """The menu bar item with its menu open, as the demo's state has it: the pose's
    own icon and the menu's own words, in a frame that imitates a light menu bar."""
    from file_librarian import i18n, menubar_state, poses
    from file_librarian.i18n import t

    shown = menubar_state.view(built.database, built.config_path)
    icon = poses.icon(shown.pose).read_text(encoding="utf-8")
    with i18n.using(built.language):
        entries = [
            ("line", shown.line),
            ("line", shown.next),
            ("sep", ""),
            ("item", t("menu.open_status").text()),
            ("item hot", f"{t('menu.review').text()} ({shown.review})"),
            ("item", f"{t('menu.due').text()} ({shown.due})"),
            ("item", t("menu.organize").text()),
            ("sep", ""),
            ("item", t("menu.settings").text()),
            ("item", t("menu.quit").text()),
        ]
    rows = "".join(
        '<div class="sep"></div>'
        if kind == "sep"
        else f'<div class="{kind}">{html.escape(text)}</div>'
        for kind, text in entries
        if kind == "sep" or text
    )
    return f"""<!doctype html><meta charset="utf-8"><style>
body {{ margin: 0; width: 520px; height: 320px; font: 13px -apple-system, system-ui;
  background: linear-gradient(135deg, #d9e4f5, #f3e7e9); }}
.bar {{ height: 26px; background: rgba(246, 246, 246, .85); display: flex;
  justify-content: flex-end; align-items: center; gap: 14px; padding: 0 14px;
  box-shadow: 0 .5px 0 rgba(0, 0, 0, .15); }}
.cat {{ display: flex; align-items: center; gap: 2px; padding: 1px 6px; border-radius: 4px;
  background: rgba(0, 0, 0, .1); font-weight: 600; }}
.cat svg {{ width: 18px; height: 18px; }}
.menu {{ position: absolute; right: 62px; top: 30px; width: 360px; padding: 5px;
  background: rgba(250, 250, 250, .96); border-radius: 8px;
  box-shadow: 0 8px 28px rgba(0, 0, 0, .22), 0 0 0 .5px rgba(0, 0, 0, .18); }}
.line {{ padding: 3px 10px; color: #6e6e6e; }}
.item {{ padding: 3px 10px; border-radius: 4px; color: #1d1d1f; }}
.hot {{ background: #3478f6; color: white; }}
.sep {{ height: 1px; margin: 5px 10px; background: rgba(0, 0, 0, .12); }}
.dim {{ color: #3c3c3c; }}
</style>
<div class="bar"><span class="cat">{icon}
<span>{shown.count or ""}</span></span><span class="dim">12:30</span></div>
<div class="menu">{rows}</div>"""


def language_pass(language: demo.Language, browser: Path, work: Path, profile: Path) -> None:
    from file_librarian.config import load_config
    from file_librarian.ui import server

    built = demo.build(work / language, language, home=work / "not-home")
    folder = OUT / language
    folder.mkdir(parents=True, exist_ok=True)
    print(f"{language}: demo in {built.root}")
    with demo.sandboxed(built.home):
        names_resolve = server.names_resolve
        # Chrome resolves .localhost names itself; the session gets its own name.
        server.names_resolve = lambda: True
        config = load_config(built.config_path)
        ui = server.Ui(config, config_path=built.config_path, poll_seconds=0.05)
        ui.start()
        try:
            handoff = ui.write_handoff()
            with headless(browser, profile / language) as (tab, _browser):
                size(tab, 900)
                dark(tab, False)
                tab.call("Page.navigate", {"url": handoff.as_uri()})
                tab.wait_for(lambda: tab.url() == f"{ui.origin}/status" and tab.ready())
                for page in PAGES:
                    tab.goto(f"{ui.origin}/{page}")
                    if page == "search":
                        query = QUERY[language]
                        tab.evaluate(f"document.querySelector('input[name=q]').value = '{query}'")
                        tab.click("form.search button[type=submit]:not([formaction])")
                        tab.wait_for(lambda: "/search?r=" in tab.url() and tab.ready())
                    capture(tab, folder / f"{page}.webp")
                tab.goto(f"{ui.origin}/review")
                dark(tab, True)
                capture(tab, folder / "review-dark.webp")
                dark(tab, False)
                menu = work / f"menu-{language}.html"
                menu.write_text(menu_html(built), encoding="utf-8")
                tab.goto(menu.as_uri())
                tab.call(
                    "Emulation.setDeviceMetricsOverride",
                    {"width": 520, "height": 320, "deviceScaleFactor": 2, "mobile": False},
                )
                data = tab.call("Page.captureScreenshot", {"format": "webp", "quality": 90})
                (folder / "menu.webp").write_bytes(base64.b64decode(data["data"]))
                print(f"  {(folder / 'menu.webp').relative_to(REPO)}")
        finally:
            ui.close()
            server.names_resolve = names_resolve


def main() -> int:
    browser = find_browser()
    if browser is None:
        print("No Chrome or Chromium found (FLIB_TEST_BROWSER_PATH names one).", file=sys.stderr)
        return 1
    work = Path(tempfile.mkdtemp(prefix="flib-shots-"))
    profile = work / "profiles"
    try:
        for language in ("en", "pt-BR"):
            language_pass(language, browser, work, profile)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
