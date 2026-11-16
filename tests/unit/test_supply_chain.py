"""Guards for the dependency supply chain. See docs/decisions/0003-supply-chain.md.

These run with the rest of the suite, so a pull request that weakens a guard
fails locally and in CI, not only after review.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import REPO_ROOT

PYPI = "https://pypi.org/simple"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def load_toml(name: str) -> dict[str, Any]:
    with (REPO_ROOT / name).open("rb") as fh:
        return tomllib.load(fh)


def test_quarantine_for_new_releases() -> None:
    uv = load_toml("pyproject.toml")["tool"]["uv"]
    assert uv["exclude-newer"] == "7 days"


def test_no_alternative_indexes_or_sources() -> None:
    uv = load_toml("pyproject.toml")["tool"]["uv"]
    for key in ("index", "extra-index-url", "index-url", "find-links", "sources"):
        assert key not in uv, f"[tool.uv] {key} needs an ADR before it is added"


def test_every_locked_package_comes_from_pypi_with_hashes() -> None:
    lock = load_toml("uv.lock")
    for package in lock["package"]:
        source = package["source"]
        if source == {"editable": "."}:
            continue  # the project itself
        assert source == {"registry": PYPI}, f"{package['name']} is not from PyPI: {source}"
        files = [*package.get("wheels", []), *([package["sdist"]] if "sdist" in package else [])]
        assert files, f"{package['name']} has no artifacts in the lock"
        for artifact in files:
            assert artifact.get("hash", "").startswith("sha256:"), (
                f"{package['name']}: artifact without sha256 hash"
            )


def workflow_files() -> list[Path]:
    return sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))


@pytest.mark.parametrize("workflow", workflow_files(), ids=lambda p: p.name)
def test_actions_are_pinned_to_full_commit_sha(workflow: Path) -> None:
    for line_no, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
        match = re.search(r"uses:\s*([^\s#]+)", line)
        if not match or match.group(1).startswith("./"):
            continue
        ref = match.group(1).rpartition("@")[2]
        assert FULL_SHA.match(ref), f"{workflow.name}:{line_no} not pinned by SHA: {line.strip()}"


@pytest.mark.parametrize("workflow", workflow_files(), ids=lambda p: p.name)
def test_workflows_avoid_privileged_triggers(workflow: Path) -> None:
    text = workflow.read_text(encoding="utf-8")
    assert "pull_request_target" not in text
    assert "persist-credentials: false" in text or "actions/checkout" not in text
