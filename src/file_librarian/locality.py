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
    content = "file names, metadata and text excerpts"
    compat = provider.name in ("openai-compat", "ollama")
    if config.vision.enabled and provider.name == "anthropic":
        content += ", and images and scanned pages"
    elif config.vision.enabled and compat and provider.vision:
        content += ", and images"
    if provider.name in ("anthropic", "claude-code"):
        via = "the Anthropic API" if provider.name == "anthropic" else "Anthropic (Claude Code)"
        found.append(Leaves(f"{content} to classify", via))
    elif compat:
        url = provider.base_url or ""
        if not is_loopback_url(url):
            found.append(Leaves(f"{content} to classify", host(url)))
    semantic = config.search.semantic
    if semantic.enabled and not is_loopback_url(semantic.base_url):
        index = "the index's names, tags and descriptions, and search queries"
        found.append(Leaves(index, host(semantic.base_url)))
    return found


def summary_lines(config: Config, *, hosts: bool = True) -> list[str]:
    """``fully local: yes|no`` and one line per thing that leaves; ``hosts=False``
    writes "a remote server" instead of a host name (for ``flib doctor``)."""
    leaving = what_leaves(config)
    if not leaving:
        return ["fully local: yes (classification, text recognition and search run on this Mac)"]
    lines = ["fully local: no"]
    for item in leaving:
        where = item.where
        if not hosts and "Anthropic" not in where:
            where = "a remote server"
        lines.append(f"  leaves this Mac: {item.what}, to {where}")
    return lines
