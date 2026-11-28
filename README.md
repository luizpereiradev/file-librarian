# file-librarian

<img src="src/file_librarian/assets/icon-1024.png" alt="file-librarian icon: an orange cat shelved between book spines" width="96">

Keeps your Mac's Downloads folder organized. Once a day, `flib` reads each new
file, gives it a descriptive name, moves it into a category folder and writes a
searchable description to a local SQLite index. **The AI only decides;
deterministic code moves files**, nothing is deleted or overwritten, and every
run can be undone.

`documento(3).pdf` becomes `Finance/2026-10-05_water-bill.pdf`, and
`flib search "water bill"` finds it months later, with its amount and due date.

**Documentation: [luizpereiradev.github.io/file-librarian](https://luizpereiradev.github.io/file-librarian/)**
([em português](https://luizpereiradev.github.io/file-librarian/pt-BR/))

![The Review page of flib ui: photos the AI was not sure about, grouped under one
suggestion, with Accept all](docs/assets/screenshots/en/review.webp)

> **Status: alpha, getting ready for v0.1.0.** Everything documented is
> implemented and tested. The first release is tagged after two weeks of daily
> use ([#24](https://github.com/luizpereiradev/file-librarian/issues/24)); until
> then, install from the repository. macOS only.

## Install

The current macOS and [uv](https://docs.astral.sh/uv/) 0.13+ (`brew install uv`):

```sh
git clone https://github.com/luizpereiradev/file-librarian.git && cd file-librarian
./scripts/install.sh
flib init                 # a few questions, a trial on copies of your files, the daily run
```

Or try it first on a fictional library, offline: `uv sync && uv run python -m
scripts.demo /tmp/flib-demo`.

## What leaves your Mac

- **To the AI provider you choose:** each new file's name, metadata and at most
  4,000 characters of its text; with Ollama, nothing at all.
- **Never:** files with credential names or secrets (left in place, never sent),
  your index, logs and undo history.
- **Only if you turn them on:** semantic search and the MCP server.

## Read more

| | |
| --- | --- |
| [Getting started](https://luizpereiradev.github.io/file-librarian/getting-started/) | requirements, install, `flib init`, the daily run, the menu bar cat |
| [Daily use](https://luizpereiradev.github.io/file-librarian/daily-use/) | review, search and `--sum`, what is due, activity and undo, watch mode, `flib ui` |
| [Privacy and safety](https://luizpereiradev.github.io/file-librarian/privacy/) | what is sent where, sensitive files, fully local with Ollama, how undo protects files |
| [Providers](https://luizpereiradev.github.io/file-librarian/providers/) | Anthropic, Claude Code, Ollama, any OpenAI-compatible API; costs and caps |
| [Configuration](https://luizpereiradev.github.io/file-librarian/configuration/) and [CLI](https://luizpereiradev.github.io/file-librarian/cli/) | every option and every command |
| [Troubleshooting](https://luizpereiradev.github.io/file-librarian/troubleshooting/) | `flib doctor`, permissions, locked-Mac runs |

The site is built from [`docs/`](docs/); the pages read fine on GitHub too.

## Contributing

Start with [CONTRIBUTING.md](CONTRIBUTING.md) and
[docs/architecture.md](docs/architecture.md). `uv run pytest` runs the whole
suite offline. Report vulnerabilities privately, as [SECURITY.md](SECURITY.md)
describes. Changes are in the [CHANGELOG](CHANGELOG.md).

## License

[MIT](LICENSE). The icon artwork (`src/file_librarian/assets/icon-1024.png`)
was AI-generated for this project.
