# 3. Guard the dependency supply chain

- Status: accepted
- Date: 2026-11-16

## Context

file-librarian runs unattended every day with access to the user's personal
files and an API key. A poisoned dependency or GitHub Action would inherit
all of that. The usual attacks are a hijacked maintainer account publishing a
malicious release, a typosquatted package added in a pull request, a moved
action tag (as in the 2025 `tj-actions/changed-files` incident), and a
workflow that leaks a token to code from a fork.

## Decision

Layered guards, each checked automatically:

| Threat | Guard | Where |
| --- | --- | --- |
| Malicious new release of a dependency | 7-day quarantine: `exclude-newer = "7 days"`; Dependabot `cooldown` of 7 days | `pyproject.toml`, `.github/dependabot.yml` |
| Tampered or swapped artifacts | `uv.lock` pins every artifact by sha256; CI installs with `uv sync --locked` and checks `uv lock --check` | `uv.lock`, CI |
| Package from an unexpected index | Only PyPI; no extra indexes, `find-links` or git/path sources | `tests/unit/test_supply_chain.py` |
| Known-vulnerable dependency | `uv audit` (OSV) on every PR, on `main` and weekly; dependency review blocks PRs that add a vulnerable package | `.github/workflows/security.yml` |
| Moved or hijacked action tag | Every action pinned to a full commit SHA; Dependabot updates the pins | workflows, test |
| Token theft from workflows | `permissions: {}` or `contents: read`, `persist-credentials: false`, no `pull_request_target`; zizmor lints workflows | workflows, test, zizmor |
| Unreviewed change to the above | CODEOWNERS on `.github/`, `pyproject.toml`, `uv.lock` and the safety-critical core | `.github/CODEOWNERS` |
| New dependency without scrutiny | Written policy (CONTRIBUTING.md): justify it, prefer the standard library | review |

Security advisories are not delayed: Dependabot security updates ignore the
cooldown, and a fix can be let through the quarantine for one package with
`uv lock --upgrade-package <name> --exclude-newer-package <name>=false`.

When releases to PyPI start (v1.0), they will use Trusted Publishing from a
dedicated workflow with build attestations, never a long-lived token.

## Consequences

- New upstream releases arrive about a week late. Acceptable for a tool that
  should change slowly.
- Adding an index, a git dependency or an unpinned action fails the test
  suite, so it cannot slip in unnoticed; it needs a new ADR.
- `uv audit` is still a preview feature of uv and is enabled explicitly; if
  its interface changes, the Security workflow fails loudly rather than
  silently passing.
