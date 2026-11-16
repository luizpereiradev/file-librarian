# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- M0 foundation: package layout, `flib` command with every planned command
  (empty until its milestone), `flib check-config`, and `flib inspect`.
- Configuration schema with validation and `config.example.yaml`. Only the
  provider is required.
- `language` option (`en`, `pt-BR`) for generated names and descriptions and
  for the default folder and category names.
- SQLite schema (migration 0001) with WAL mode and an accent-insensitive FTS5 index.
- Prototype reader for the macOS download origin (`kMDItemWhereFroms`).
- Provider interface with an offline `FakeProvider`; extractor interface and registry.
- CI on macOS: lint, format, type check and tests.
- Contributor documents, extension guides and ADRs 0001–0002.

### Security

- Supply-chain guards (ADR 0003): 7-day quarantine for new releases,
  Dependabot with cooldown, `uv audit` and dependency review in a Security
  workflow, actions pinned by commit SHA, zizmor workflow linting, CODEOWNERS,
  and tests that fail on unpinned actions or non-PyPI sources.
