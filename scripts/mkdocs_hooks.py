"""MkDocs hooks for the site (``hooks:`` in mkdocs.yml); no plugin needed.

The Brazilian Portuguese site is built from ``docs/pt-BR/`` but shows the same
images as the English one: this hook adds ``docs/assets/`` and ``docs/images/`` to
its build, so both sites reference them the same way (``assets/...``) and the
repository keeps one copy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mkdocs.structure.files import File, Files

SHARED = ("assets", "images")


def on_files(files: Files, config: Any) -> Files:
    docs = Path(config["docs_dir"])
    if docs.name != "pt-BR":
        return files
    root = docs.parent
    for folder in SHARED:
        for path in sorted((root / folder).rglob("*")):
            if path.is_file() and not path.name.startswith("."):
                uri = path.relative_to(root).as_posix()
                files.append(File(uri, str(root), config["site_dir"], config["use_directory_urls"]))
    return files
