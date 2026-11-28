"""Whether a configuration keeps everything on this Mac, and if not, what leaves it (#92).

``flib check-config`` and ``flib doctor`` print the answer as "fully local:
yes" or "fully local: no", followed by one line per thing that leaves.

What can leave, by setting:

- ``provider``: the classification request (file name, metadata and the
  text excerpt; with ``vision.enabled``, images and scanned pages too) goes to
  Anthropic for ``anthropic`` and ``claude-code``, and to ``provider.base_url``
  for ``openai-compat`` and ``ollama`` unless that is a loopback address.
- ``search.semantic``: the index's text (names, categories, tags,
  descriptions) and every semantic query go to ``search.semantic.base_url``,
  unless it is a loopback address.

Never anything else: text recognition (``ocr``) runs on macOS's own engine,
the index and logs stay in flib's folder, and the MCP server talks only to the
app that started it.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from file_librarian.config import Config, is_loopback_url
from file_librarian.i18n import english, t


@dataclass(frozen=True, slots=True)
class Leaves:
    what: str
    """What leaves the Mac, as a phrase."""
    where: str
    """Where it goes: a company, or a server's host name."""


def host(url: str) -> str:
    return urlsplit(url).hostname or url


def what_leaves(config: Config) -> list[Leaves]:
    """Everything this configuration sends off this Mac; empty when nothing does."""
    found: list[Leaves] = []
    provider = config.provider
    content = t("term.local.content")
    compat = provider.name in ("openai-compat", "ollama")
    if config.vision.enabled and provider.name == "anthropic":
        content = t("term.local.content_pages")
    elif config.vision.enabled and compat and provider.vision:
        content = t("term.local.content_images")
    classify = t("term.local.to_classify", content=content)
    if provider.name in ("anthropic", "claude-code"):
        via = t("term.local.api" if provider.name == "anthropic" else "term.local.claude_code")
        found.append(Leaves(classify, via))
    elif compat:
        url = provider.base_url or ""
        if not is_loopback_url(url):
            found.append(Leaves(classify, host(url)))
    semantic = config.search.semantic
    if semantic.enabled and not is_loopback_url(semantic.base_url):
        found.append(Leaves(t("term.local.index"), host(semantic.base_url)))
    return found


def summary_lines(config: Config, *, hosts: bool = True) -> list[str]:
    """``fully local: yes|no`` and one line per thing that leaves; ``hosts=False``
    writes "a remote server" instead of a host name (for ``flib doctor``)."""
    leaving = what_leaves(config)
    if not leaving:
        return [t("term.local.yes")]
    lines: list[str] = [t("term.local.no")]
    for item in leaving:
        where = item.where
        if not hosts and "Anthropic" not in english(where):
            where = t("term.local.remote")
        lines.append(t("term.local.leaves", what=item.what, where=where))
    return lines
