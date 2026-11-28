# 5. A documentation site in two languages, built with MkDocs

- Status: accepted
- Date: 2026-11-28

## Context

v0.1.0 ships with a presentation video, and the README had grown to about
1,100 lines: everything a user needs, in one page that is hard to skim and
impossible to read in Portuguese. The audience is developers on macOS; the
first users write in Brazilian Portuguese. The site must be cheap to keep
true: the CLI changes, the interface changes, and screenshots rot.

Constraints: every dependency is guarded (ADR 0003): locked, from PyPI only,
behind the 7-day quarantine, and justified. Workflows are pinned by SHA,
least privilege, and linted by zizmor.

## Decision

**Generator.** MkDocs 1.6 with the Material theme, in a `docs` dependency
group of its own (`uv sync --group docs`), never in the runtime or the test
environment. Material brings MkDocs, Python-Markdown, pymdown-extensions,
Jinja2, Babel and `requests` (24 locked packages, all MIT, BSD or Apache
licensed). They run only to build HTML from Markdown, in CI and on a
contributor's machine; nothing of them is installed with flib. MkDocs 1.x is
in maintenance and Material is too: they are stable, the site uses no plugin,
and moving to a successor later means moving Markdown, not code.

**Two languages, two builds, no plugin.** English is built from `docs/` into
`site/`, Brazilian Portuguese from `docs/pt-BR/` into `site/pt-BR/`
(`mkdocs.pt-BR.yml` inherits `mkdocs.yml`). Material's language selector
(`extra.alternate`) links them. A ten-line hook (`scripts/mkdocs_hooks.py`)
lets the Portuguese build use the same images, so the repository keeps one
copy. The `mkdocs-static-i18n` plugin would do the same with one more
dependency and a more complex configuration; two builds need none. The
Portuguese navigation lists translated pages only; pages that exist only in
English (the guide, the architecture, the contributor docs) are linked from
the text, marked "(em inglês)". Translated headings keep the English anchor
(`{ #anchor }`), so links between pages are the same in both languages.

**One source for each fact.** The README is a one-screen landing page; what
it documented moved to the site. The CLI reference is generated from the
typer app (`scripts/docs_cli.py`) and a test fails when it is out of date;
its Portuguese help texts live in `scripts/cli_pt_BR.json` with the English
they were translated from, so a changed help text fails the same test. The
full configuration reference includes `config.example.yaml` itself. The
terminal samples and every screenshot come from the demo library
(`scripts/demo.py`), by scripts, never from a real Mac's data.

**Checking and publishing.** The CI workflow builds both sites in strict mode
(a broken link or anchor, or a page left out of the navigation, fails) in a
`docs` job on Linux, and the required check `CI passed` needs it: the site is
the documentation now, so a pull request that breaks it does not merge. The
job runs beside the macOS tests and adds nothing to the time CI takes.
`.github/workflows/docs.yml` builds again on every push to `main` and deploys
to GitHub Pages with the official `actions/upload-pages-artifact` and
`actions/deploy-pages`, pinned by SHA; only its deploy job gets `pages: write`
and `id-token: write`. The site loads no web fonts and no analytics.

## Consequences

- A change to the CLI's help needs `uv run python -m scripts.docs_cli`, and a
  Portuguese translation of the changed text, or the test suite fails.
- A page added to `docs/` must be added to the navigation (or to `not_in_nav`),
  and a new public page needs its translation before it is linked from the
  Portuguese navigation.
- Screenshots and samples are refreshed by running two scripts; their dates
  follow the day they run.
- A docs-only failure blocks a merge like a failing test. That is the point,
  and the build takes about a minute.
