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
import yaml

from tests._helpers import REPO_ROOT

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


GITHUB = REPO_ROOT / ".github"
# Triggers that run with secrets or a write token on code from forks.
FORBIDDEN_TRIGGERS = {"pull_request_target", "workflow_run"}


def workflow_files() -> list[Path]:
    workflows = GITHUB / "workflows"
    return sorted([*workflows.glob("*.yml"), *workflows.glob("*.yaml")])


def load_workflow(path: Path) -> dict[Any, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name} is not a mapping"
    return data


def triggers(workflow: dict[Any, Any]) -> set[str]:
    # YAML 1.1 reads a bare `on:` key as the boolean True.
    on = workflow.get("on", workflow.get(True))
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return set(on)
    if isinstance(on, dict):
        return set(on)
    return set()


def steps(workflow: dict[Any, Any]) -> list[dict[str, Any]]:
    return [step for job in (workflow.get("jobs") or {}).values() for step in job.get("steps", [])]


def test_workflows_exist() -> None:
    assert workflow_files(), "no workflows found; did the layout change?"


@pytest.mark.parametrize("workflow", workflow_files(), ids=lambda p: p.name)
def test_actions_are_pinned_to_full_commit_sha(workflow: Path) -> None:
    data = load_workflow(workflow)
    uses = [step["uses"] for step in steps(data) if "uses" in step]
    uses += [job["uses"] for job in (data.get("jobs") or {}).values() if "uses" in job]
    for ref in uses:
        if ref.startswith("./"):
            continue
        assert FULL_SHA.match(ref.rpartition("@")[2]), f"{workflow.name}: not pinned by SHA: {ref}"


@pytest.mark.parametrize("workflow", workflow_files(), ids=lambda p: p.name)
def test_workflows_avoid_privileged_triggers(workflow: Path) -> None:
    found = triggers(load_workflow(workflow)) & FORBIDDEN_TRIGGERS
    assert not found, f"{workflow.name} uses {found}; needs an ADR and a security review"


@pytest.mark.parametrize("workflow", workflow_files(), ids=lambda p: p.name)
def test_every_checkout_drops_credentials(workflow: Path) -> None:
    for step in steps(load_workflow(workflow)):
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            options = step.get("with") or {}
            assert options.get("persist-credentials") is False, (
                f"{workflow.name}: checkout step without persist-credentials: false"
            )


@pytest.mark.parametrize("workflow", workflow_files(), ids=lambda p: p.name)
def test_workflow_token_is_read_only_by_default(workflow: Path) -> None:
    permissions = load_workflow(workflow).get("permissions")
    assert permissions in ({}, {"contents": "read"}), (
        f"{workflow.name}: top-level permissions must be {{}} or contents: read"
    )


def test_install_script_uses_the_lockfile() -> None:
    script = (REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert "uv export --frozen" in script
    assert "--overrides" in script
    assert "--extra mcp" in script  # the optional MCP server is locked too


def test_guards_are_owned() -> None:
    owners = (GITHUB / "CODEOWNERS").read_text(encoding="utf-8")
    for path in (
        "/.github/",
        "/pyproject.toml",
        "/uv.lock",
        "/scripts/",
        "/tests/conftest.py",
        "/tests/unit/test_supply_chain.py",
        "/SECURITY.md",
        "/docs/decisions/",
    ):
        assert any(
            line.split()[0] == path
            for line in owners.splitlines()
            if line.strip() and not line.startswith("#")
        ), f"CODEOWNERS does not cover {path}"
