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

The quarantine cannot tell a fix from an attack, so it also delays security
fixes. Dependabot's security updates skip its own cooldown, but they lock with
uv, which still refuses a release younger than 7 days. For an urgent fix the
maintainer adds a temporary, reviewed exception for that one package
(`exclude-newer-package = { <name> = false }` under `[tool.uv]`) and removes it
once the release is older than the quarantine. The procedure is in
[SECURITY.md](https://github.com/luizpereiradev/file-librarian/blob/main/SECURITY.md#urgent-security-fixes).

When releases to PyPI start (v1.0), they will use Trusted Publishing from a
dedicated workflow with build attestations, never a long-lived token.

## Consequences

- New upstream releases arrive about a week late. Acceptable for a tool that
  should change slowly.
- Adding an index, a git dependency, an unpinned action, a checkout that keeps
  credentials or a `pull_request_target`/`workflow_run` trigger fails the test
  suite. The tests are not tamper-proof: a pull request could weaken a test and
  the rule it checks together. That is why CODEOWNERS covers the tests, the
  workflows and this ADR, and why `main` requires review before merging.
- `uv audit` is still a preview feature of uv and is enabled explicitly; if
  its interface changes, the Security workflow fails loudly rather than
  silently passing.
