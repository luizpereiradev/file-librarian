# Contributing, security and changes

## Contributing

Contributions are welcome. Start with
[CONTRIBUTING.md](https://github.com/luizpereiradev/file-librarian/blob/main/CONTRIBUTING.md),
the [architecture](architecture.md) and the
[requirements](requirements.md). Adding a file format or an AI provider
has its own guide: [adding an extractor](adding-an-extractor.md)
and [adding a provider](adding-a-provider.md). `uv run pytest`
runs the whole suite offline, in parallel on every core (`-n 0` for one
process). Questions go to
[Discussions](https://github.com/luizpereiradev/file-librarian/discussions).

This site is built from `docs/` with MkDocs and the Material theme:

```sh
uv run --group docs python -m scripts.docs serve     # build both languages, serve at http://127.0.0.1:8000
```

English pages are in `docs/`, their Brazilian Portuguese versions in
`docs/pt-BR/` (same file names). The CLI reference is generated from the
command line itself (`uv run python -m scripts.docs_cli`), the screenshots and
terminal samples from the [demo library](getting-started.md#try-it-on-a-demo-library)
(`uv run python -m scripts.screenshots`, `uv run python -m scripts.docs_samples`).

## Security

Report vulnerabilities privately, as
[SECURITY.md](https://github.com/luizpereiradev/file-librarian/blob/main/SECURITY.md)
describes, never in a public issue. It also says what is in scope and how the
dependency supply chain is guarded ([ADR 0003](decisions/0003-supply-chain.md)).
What flib sends, stores and protects is on [Privacy and safety](privacy.md).

## Changelog

[CHANGELOG.md](https://github.com/luizpereiradev/file-librarian/blob/main/CHANGELOG.md)
lists what is in each release, and what is in the upcoming one.

## Roadmap

Everything planned for M0–M3 and the P2 backlog is implemented; the
[requirements](requirements.md) have each one's status. Next: v0.1.0
after the daily-use validation, then packaging for v1.0 (a release on PyPI).

## License

[MIT](https://github.com/luizpereiradev/file-librarian/blob/main/LICENSE). The
icon artwork (`src/file_librarian/assets/icon-1024.png`) was AI-generated for
this project.
