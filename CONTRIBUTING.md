# Contributing to file-librarian

Thanks for helping. This guide should get you from clone to a passing test run
in under 10 minutes, with no API key.

## Setup

You need the current major release of macOS and
[uv](https://docs.astral.sh/uv/) 0.13 or newer (`brew install uv`; check with
`uv --version`). uv installs the right Python (3.11+) for you.

```sh
git clone https://github.com/luizpereiradev/file-librarian.git && cd file-librarian
uv sync              # creates .venv with the package and dev tools
uv run pytest        # all tests, offline, in temporary folders
```

Before opening a pull request, run the same checks as CI:

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy          # strict, over src/ and tests/
uv run coverage run -m pytest && uv run coverage report   # fails under 93% branch coverage
```

A new branch of behaviour, especially a failure path, comes with a test.
`coverage report` lists the missing lines.

`uv run ruff format .` fixes formatting; `uv run ruff check --fix .` fixes most lint findings.

## Questions

Ask in [Discussions](https://github.com/luizpereiradev/file-librarian/discussions).
Issues are for confirmed bugs and feature proposals.

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

- **Tests never use the network or your files.** Use `FakeProvider` with fixed
  answers. The suite blocks network connections and DNS and points `HOME` at a
  temporary folder, so anything built from `~` or `Path.home()` stays there.
  Never hard-code an absolute path to a real folder in a test.
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

- A feature issue cites a requirement ID (`RF-xx`) from
  [docs/requirements.md](docs/requirements.md), or `new`. Small, well-scoped
  issues get the `good first issue` label.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`).
- Keep pull requests focused, fill in the template, and add a line under
  `Unreleased` in [CHANGELOG.md](CHANGELOG.md) for user-visible changes.
- Pull requests are squash-merged; the PR title becomes the commit message on
  `main`, so write it as a Conventional Commit.
- Versions follow [Semantic Versioning](https://semver.org/).

## Releases

There are no releases before v1.0's packaging work. When there are, the
maintainer will: move the `Unreleased` entries in CHANGELOG.md under the new
version, bump `version` in `pyproject.toml`, merge that as `chore: release
vX.Y.Z`, tag the merge commit `vX.Y.Z`, and publish from a dedicated workflow
with PyPI Trusted Publishing (ADR 0003). No one publishes from a laptop.

## Conduct and security

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md). Report
vulnerabilities privately as described in [SECURITY.md](SECURITY.md), not in a
public issue.
