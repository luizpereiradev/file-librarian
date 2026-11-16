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
- A dependency, GitHub Action or workflow that could run untrusted code or leak a token

## Supply chain

Every dependency and GitHub Action can run code on users' machines or in CI,
so they are treated as part of the attack surface:

- **Quarantine.** Releases younger than 7 days are ignored when locking
  (`exclude-newer` in `pyproject.toml`), and Dependabot waits the same 7 days.
- **Locked and hashed.** `uv.lock` pins every artifact by sha256, only PyPI is
  allowed, and CI installs with `uv sync --locked`.
- **Audited.** `uv audit` checks the lock against OSV on every pull request, on
  `main` and weekly; dependency review blocks pull requests that add a
  vulnerable package.
- **Pinned actions.** Workflows reference actions by full commit SHA, run with
  read-only permissions, and are linted by zizmor.

## Urgent security fixes

The quarantine also delays fixes: a patched release younger than 7 days is
refused, including in Dependabot's security pull requests. When an advisory
cannot wait, a maintainer lets that one package through:

1. Add a temporary exception under `[tool.uv]` in `pyproject.toml`, with the
   advisory ID and a removal date in a comment:

   ```toml
   # GHSA-xxxx-xxxx-xxxx: remove after 2026-11-30
   exclude-newer-package = { <name> = false }
   ```

2. Run `uv lock --upgrade-package <name>`, check that only that package (and
   anything it strictly requires) changed in `uv.lock`, and open a pull request
   citing the advisory.
3. Once the release is older than 7 days, remove the exception in a follow-up
   pull request; `uv lock --check` must still pass.

The full rationale is in [ADR 0003](docs/decisions/0003-supply-chain.md). If
you suspect a compromised dependency, report it privately as above.

## Supported versions

Until v1.0 only the latest release of file-librarian receives fixes, and only
on the current major release of macOS.
