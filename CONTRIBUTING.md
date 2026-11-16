# Contributing to file-librarian

Thanks for helping. This guide should get you from clone to a passing test run
in under 10 minutes, with no API key.

## Setup

You need the current major release of macOS and
[uv](https://docs.astral.sh/uv/) (`brew install uv`). uv installs the right
Python (3.11+) for you.

```sh
git clone https://github.com/luizpereiradev/file-librarian.git && cd file-librarian
uv sync              # creates .venv with the package and dev tools
uv run pytest        # all tests, offline, in temporary folders
```

Before opening a pull request, run the same checks as CI:

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

`uv run ruff format .` fixes formatting; `uv run ruff check --fix .` fixes most lint findings.

## Where things go

The code mirrors the pipeline; read [docs/architecture.md](docs/architecture.md) first.

| You want to... | Look in |
| --- | --- |
| Support a new file format | `src/file_librarian/extractors/`, guide in [docs/adding-an-extractor.md](docs/adding-an-extractor.md) |
| Add an AI provider | `src/file_librarian/providers/`, guide in [docs/adding-a-provider.md](docs/adding-a-provider.md) |
| Change the database schema | add a new `src/file_librarian/db/migrations/NNNN_*.sql`; never edit a published one |
| Add a configuration option | `config.py` and `config.example.yaml`, together |
| Add a language | `locales.py`, plus a test in `tests/unit/test_config.py` |
| Change the architecture | write an ADR in [docs/decisions/](docs/decisions/) first |

## Rules

- **Tests never use the network.** Use `FakeProvider` with fixed answers. The
  test suite blocks sockets and points `HOME` at a temporary folder, so a test
  that tries to reach an API or your real Downloads fails.
- **No personal files.** Fixtures are synthetic. Generate them in code when you can.
- **The model returns data, code acts.** Providers never touch the file
  system; the planner validates every destination.
- **Nothing is deleted or overwritten.** Any change to the executor must keep
  that guarantee and come with a test proving it.
- **English** for code, comments, commits, issues, pull requests and docs.
- **Current macOS only.** Only the latest major macOS release is supported, and
  changes for other platforms will not be accepted in v1.

## Dependencies

Every dependency runs with access to the user's files, so the bar is high
([ADR 0003](docs/decisions/0003-supply-chain.md)):

- **Prefer the standard library.** A new dependency needs a reason in the pull
  request: what it does that a few lines of our own code cannot.
- **Check what you add.** Exact name (typosquats are common), an active
  project with more than one maintainer when possible, a license compatible
  with MIT, and few transitive dependencies.
- **Use uv, commit the lock.** `uv add <package>` (or `uv add --dev`), then
  commit `pyproject.toml` and `uv.lock` together. Never hand-edit `uv.lock`.
- **No new sources.** Only PyPI. Extra indexes, git or path dependencies fail
  the test suite.
- **Releases younger than 7 days are ignored** by design, security fixes
  included. If a fix cannot wait, follow
  [Urgent security fixes](SECURITY.md#urgent-security-fixes) in SECURITY.md.
- **Actions are pinned by commit SHA** with the version in a comment. Let
  Dependabot bump them; do not replace a SHA with a tag.

## Issues and pull requests

- A feature issue cites the requirement ID from the PRD (`RF-xx`). Small,
  well-scoped issues get the `good first issue` label.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`).
- Keep pull requests focused, fill in the template, and add a line under
  `Unreleased` in [CHANGELOG.md](CHANGELOG.md) for user-visible changes.
- Versions follow [Semantic Versioning](https://semver.org/).

## Conduct and security

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md). Report
vulnerabilities privately as described in [SECURITY.md](SECURITY.md), not in a
public issue.
