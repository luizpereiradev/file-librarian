# Contributing to file-librarian

Thanks for helping. This guide should get you from clone to a passing test run
in under 10 minutes, with no API key.

## Setup

You need the current major release of macOS and
[uv](https://docs.astral.sh/uv/) 0.13 or newer (`brew install uv`; check with
`uv --version`). uv installs the right Python (3.11+) for you.

```sh
git clone https://github.com/luizpereiradev/file-librarian.git && cd file-librarian
uv sync              # creates .venv with the package, the optional MCP SDK and dev tools (use `uv run flib ...`)
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

### What CI runs

CI runs those checks on `macos-15`. A pull request is tested on the oldest
and newest supported Python, 3.11 and 3.15; every push to `main` is tested on
3.11 through 3.15. Python 3.15 also runs the cross-volume tests on real disk
images (APFS, HFS+, exFAT); run them locally with
`FLIB_TEST_VOLUMES=1 uv run pytest -m volumes`. The one required check is
`CI passed`, which passes only when every Python version passed. A separate
Security workflow reviews new dependencies, audits `uv.lock` and lints the
workflows (see [SECURITY.md](SECURITY.md#supply-chain)).

## Your first contribution

1. Run the setup and the four checks above, so you know they pass before you
   change anything.
2. Read [docs/architecture.md](docs/architecture.md) (ten minutes) and
   "Writing tests" below.
3. Pick one:
   - **a file format** flib does not read yet:
     [docs/adding-an-extractor.md](docs/adding-an-extractor.md).
     `extractors/mail.py` is a complete example;
   - **an AI provider:** [docs/adding-a-provider.md](docs/adding-a-provider.md);
   - or an issue labelled `good first issue`.
4. Open a draft pull request early; the guide's checklist goes in its
   description.

## Writing tests

- **Helpers** (plain functions and classes) live in `tests/_helpers.py`:
  `put` and `make_old` for stable files in the fake Downloads, `fake_config`
  and `write_config` for configurations, `organize`, `answer`, `row_for` and
  `rows` for a real run over the fake provider, `make_pdf`/`make_docx`/... for
  synthetic documents, `mark_cloud_only`, `set_tags`, `Crash`, and
  `FakeClient`/`reply` for the Anthropic API.
- **Fixtures** live in `tests/conftest.py`. The safety guards there are
  autouse: no network or DNS, a temporary `HOME`, and fakes of the Keychain,
  launchctl, sips and `claude` (the `tests/fake_*.py` scripts), and of the
  screen lock and Data Protection classes (`fake_protection`, a
  `FakeProtection` from `tests/_helpers.py`: `protect` and `lock_screen`
  make a file unreadable while the fake Mac is locked). `downloads`
  and `cfg` give a test a Downloads folder and the fake provider's config;
  `fake_openai` (opt-in) runs an OpenAI-compatible server on 127.0.0.1 for
  that test only.
- **Never call `monkeypatch.undo()`.** The safety guards use the same
  `monkeypatch` as the test, so `undo()` switches them off for the rest of it.
  To patch something for part of a test, use
  `with monkeypatch.context() as m:` and `m.setattr(...)`; only those patches
  are reverted when the block ends. `tests/unit/test_test_guards.py` enforces
  this.
- A helper needed by more than one test module goes into `tests/_helpers.py`;
  test modules never import from each other.

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
| Add a configuration option | `config.py` and `config.example.yaml`, together; one that does nothing yet goes into `INACTIVE_OPTIONS` in `config.py` until the PR that activates it |
| Add a language | `locales.py`, plus a test in `tests/unit/test_config.py` |
| Change the architecture | write an ADR in [docs/decisions/](docs/decisions/) first |

## Rules

- **Tests never use the network or your files.** Use `FakeProvider` with fixed
  answers. The suite blocks network connections and DNS and points `HOME` at a
  temporary folder, so anything built from `~` or `Path.home()` stays there.
  The macOS Keychain is replaced by a throwaway fake (`tests/fake_security.py`).
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

The maintainer cuts releases by hand, following
[docs/releasing.md](docs/releasing.md). There is no release workflow and
nothing is published to PyPI yet: users install from the repository with
`scripts/install.sh`, pinned to `uv.lock`. When PyPI publishing starts (v1.0),
it will use a dedicated workflow with Trusted Publishing
([ADR 0003](docs/decisions/0003-supply-chain.md)); no one publishes from a
laptop.

## Conduct and security

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md). Report
vulnerabilities privately as described in [SECURITY.md](SECURITY.md), not in a
public issue.
