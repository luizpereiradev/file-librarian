# file-librarian

Keeps your Downloads folder organized. Once a day, `flib` reads each new file,
gives it a descriptive name, moves it into a category folder and writes a
searchable description to a local SQLite index.

`documento(3).pdf` becomes `Finance/2026-10-05_electricity-invoice.pdf`, and
`flib search "electricity october"` finds it months later.

**The AI only decides. Deterministic code moves files.** These are the design
guarantees the organizing commands are built to keep from M1 on:

- nothing is deleted and nothing is overwritten;
- every run is recorded and can be undone, except files you changed or moved
  afterwards, which are skipped and listed.

> **Status: pre-alpha (milestone M0).** The command surface, configuration,
> database schema and download-origin reader exist; organizing files does not
> yet, so none of the guarantees above are exercised today. See the
> [roadmap](#roadmap).

## Requirements

- The current major release of macOS (the only supported platform)
- [uv](https://docs.astral.sh/uv/) 0.13 or newer (`brew install uv`); it
  installs Python 3.11+ for you
- An Anthropic API key for the default provider (from M1)

## Try it in 5 minutes

```sh
git clone https://github.com/luizpereiradev/file-librarian.git && cd file-librarian
uv sync                                   # installs Python 3.11 and dependencies if needed
mkdir -p ~/.config/file-librarian
cp config.example.yaml ~/.config/file-librarian/config.yaml
uv run flib check-config                  # validates your configuration
uv run flib inspect ~/Downloads/<a-file>  # read-only: hash, size and download origin
```

## Commands

| Command | What it does | Milestone |
| --- | --- | --- |
| `flib check-config` | Validate the configuration without touching files | M0 |
| `flib inspect FILE...` | Show hash, size and download origin | M0 |
| `flib simulate` | Print the full plan without changing anything | M1 |
| `flib run` | Classify, rename, move and index new files | M1 |
| `flib search QUERY [--json]` | Full-text search over names, descriptions and tags | M1 |
| `flib undo [--run ID \| --file PATH]` | Revert a run or one file | M1 |
| `flib schedule` | Install the daily launchd agent | M2 |
| `flib reconcile` | Update the index after manual moves | M2 |
| `flib init` | Guided first run on a sample | M2 |
| `flib mcp` | Local MCP server for AI assistants | M2 |
| `flib reprocess` | Reclassify after a rules change | M3 |
| `flib doctor` | Redacted logs for bug reports | M3 |

## Configuration

Everything you can tune lives in one YAML file; see the commented
[`config.example.yaml`](config.example.yaml). Set `language` to `en` or
`pt-BR` to choose the language of generated names and descriptions and of the
default folders (`Organized`/`_Review` or `Organizado`/`_Revisar`) and
categories. Only the provider is required; everything else has a default.

The categories are a closed list, and each category's `hint` is the main lever
for fixing wrong classifications.
The API key is read from `ANTHROPIC_API_KEY`, never from the file.

## Privacy

M0 sends nothing anywhere: no command talks to an AI provider yet. From M1 on,
the design is:

- only a capped excerpt of each file (`provider.max_chars_per_file`) is sent
  to the AI provider;
- files matching `never_send`, or in which secrets are detected, are
  classified from metadata only and never sent;
- a local provider (Ollama) is planned for M3 for anyone who wants nothing to
  leave the machine.

## Roadmap

| Milestone | Delivers |
| --- | --- |
| M0 Foundation | Repository, CI, database schema, empty CLI commands, contributor docs, download-origin prototype |
| M1 MVP | All P0 requirements with the `anthropic` provider |
| M2 Daily use | Scheduling, reconcile, init, images and scanned PDFs, Finder tags, learning from corrections, iCloud, MCP server, `claude-code` provider |
| M3 Open | `ollama` and `openai-compat` providers, search filters, reprocess, doctor, extension guides |
| v1.0 | Duplicates, optional semantic search, periodic summary, PyPI release |

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md),
[docs/architecture.md](docs/architecture.md) and the
[requirements](docs/requirements.md). Adding an AI provider or a file format
has its own guide in [docs/](docs/). Questions go to
[Discussions](https://github.com/luizpereiradev/file-librarian/discussions).

## License

[MIT](LICENSE)
