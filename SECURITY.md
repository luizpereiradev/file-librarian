# Security policy

file-librarian moves personal files without supervision and sends excerpts to
an AI provider, so security reports are taken seriously.

## Reporting a vulnerability

Do not open a public issue. Use GitHub's private reporting instead:
[report a vulnerability](https://github.com/luizpereiradev/file-librarian/security/advisories/new)
(**Security → Report a vulnerability** on the repository page).

Please include the version (`flib --version`), your macOS version, and the
steps to reproduce. Leave out real file names, paths and contents; synthetic
examples are enough. You should get a first answer within 7 days.

## In scope

- A file deleted or overwritten, or an undo that loses data
- A destination outside the configured category folders (path traversal,
  symlinks, crafted names or model answers)
- File content that makes the model, or an assistant using the MCP server,
  trigger an action it should not (prompt injection)
- Content sent to a provider despite `never_send` or secret detection
- API keys written to logs, the database or the configuration file

## Supported versions

Until v1.0 only the latest release of file-librarian receives fixes, and only
on the current major release of macOS.
