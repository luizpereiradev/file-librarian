# Use it from Claude (MCP)

`flib mcp` is a local [MCP](https://modelcontextprotocol.io) server over
stdio, so Claude (or any MCP client) can answer "where is last month's power
bill?" from your library. It runs only while the client is open and reads the
same database as `flib search`. It uses no network itself, except that with
semantic search on and a remote `search.semantic.base_url`, a search by
meaning sends the query there (the `search_files` tool then says so).

| Tool | What it does |
| --- | --- |
| `search_files` | Search names, descriptions and tags, with the same category, extension, period, issuer, kind, amount, currency, due date and document period filters as `flib search`; `semantic: true` also searches by meaning ([Search by meaning](daily-use.md#search-by-meaning-optional)); duplicates only with `include_duplicates` |
| `summarize_amounts` | Add up the amounts of the matching documents per currency, optionally by issuer, month, year, kind or category, like `flib search --sum` ("what did I pay to Power Co in 2026?") |
| `list_due` | Bills, invoices and taxes with a due date, overdue and coming up, with totals per currency, like `flib due` ("what is due this week?") |
| `get_file` | Everything the index knows about one file: where it is and was, category, description, tags, kind, issuer, amount, dates and period, hash, size, download origin, whether it is only in iCloud, why it is in review (if it is), and its history (moves, renames, fixes, undos with when each was undone; newest first, last 20) |
| `list_recent` | The files organized most recently, newest first, optionally in one category |
| `list_review` | What waits in the review folder, newest first, with the AI's confidence and why each file is there |
| `get_stats` | Counts by status and by category, total size filed, the last run and its provider cost, and this month's total cost |
| `recategorize_file` | Move a file to another configured category and/or rename it, like `flib fix` |
| `undo` | Revert a run or the last move of one file, like `flib undo` |

A file in review says why it is there: `low_confidence` (the AI's confidence
was below `limits.confidence_threshold`), `unknown_category` (the AI chose a
category you have not configured), `category_removed` (`flib reprocess
--remove-category --to-review`) or `moved_by_hand` (`flib reconcile` found it
in the review folder). The first two come with the category the AI suggested.

Costs are estimates, recorded per run; a run at unknown prices has no recorded
cost rather than a cost of zero, and `get_stats` counts those runs apart
([Costs and caps](providers.md#costs-and-caps)).

Results carry metadata, descriptions and the fields the AI read in each
document, never file content; sensitive files show no description, tags,
fields or origin, and their amounts are never added up. The write tools go through the same
journal as the CLI, so `flib undo` reverts what an assistant did, and they
refuse to run while a run is in progress. Set `mcp.read_only: true` to offer
only the read tools.

## 1. Enable it

The MCP server is optional, because its SDK adds 16 packages that the rest of
flib does not need. Answer yes when `./scripts/install.sh` asks, or run it
again with the flag:

```sh
./scripts/install.sh --with-mcp
```

Later updates keep it; `--without-mcp` removes it. Without it, `flib mcp`
says how to enable it.

## 2. Add it to Claude Code

```sh
claude mcp add --scope user file-librarian -- ~/.local/bin/flib mcp
```

## Or to Claude Desktop

Add this to
`~/Library/Application Support/Claude/claude_desktop_config.json` and restart
it. Desktop does not expand `~` or read your shell's `PATH`, so give the
absolute path that `./scripts/install.sh` printed:

```json
{
  "mcpServers": {
    "file-librarian": {
      "command": "/Users/you/.local/bin/flib",
      "args": ["mcp"]
    }
  }
}
```

Add `"--config", "/path/to/config.yaml"` to `args` if your configuration is
not in the default place.

What an assistant sees, and where it sends it, is in
[Privacy and safety](privacy.md#what-leaves-your-mac).
