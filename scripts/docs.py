"""Build or preview the documentation site, both languages.

    uv run --group docs python -m scripts.docs build   # site/ (English) and site/pt-BR/, strict
    uv run --group docs python -m scripts.docs serve   # build, then serve at http://127.0.0.1:8000

Two MkDocs builds, English first (it empties ``site/``), then Brazilian
Portuguese into ``site/pt-BR/``. Both are strict: a broken link, a missing
anchor or a page left out of the navigation fails the build. ``serve`` binds to
127.0.0.1 only and does not rebuild on change: run it again after editing.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIGS = ("mkdocs.yml", "mkdocs.pt-BR.yml")


def build() -> int:
    for config in CONFIGS:
        command = [sys.executable, "-m", "mkdocs", "build", "--strict", "-f", config]
        # Material's notice about its end of life (May 2027): see docs/decisions/0005.
        env = os.environ | {"NO_MKDOCS_2_WARNING": "1"}
        result = subprocess.run(command, cwd=REPO, env=env, check=False)  # noqa: S603 - our build
        if result.returncode != 0:
            return result.returncode
    print(f"Built {REPO / 'site' / 'index.html'}")
    return 0


def serve(port: int) -> int:
    code = build()
    if code != 0:
        return code
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(REPO / "site"))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        print(f"Serving http://127.0.0.1:{port}/ (Ctrl-C stops)")
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.docs")
    parser.add_argument("action", choices=("build", "serve"))
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    return build() if args.action == "build" else serve(args.port)


if __name__ == "__main__":
    raise SystemExit(main())
