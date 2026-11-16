# 2. Current macOS only, and a user-selected language

- Status: accepted
- Date: 2026-11-16

## Context

The PRD left two questions open before M0: the minimum macOS version, and
whether default folder names (such as the review folder) are in English or
Portuguese. The repository itself is in English, but the first users write
in Portuguese and expect names like `_Revisar` and `Financeiro`.

## Decision

**Platform.** Only the current major release of macOS is supported. CI runs
on `macos-latest`. Bugs that only occur on older releases are not fixed.

**Language.** The configuration has a `language` option, starting with `en`
(default) and `pt-BR`. It sets the language of generated file names and
descriptions and the defaults for `output_root` (`~/Downloads/Organized` or
`~/Downloads/Organizado`), `review_folder` (`_Review` or `_Revisar`) and the
category list. Values set explicitly in the configuration always win. The
tool's messages, code and documentation stay in English. Languages live in
`src/file_librarian/locales.py`.

**Destination.** Organized files stay inside Downloads by default, so moves
never cross volumes in the common case.

## Consequences

- One CI target and no compatibility shims for older macOS APIs.
- Users on an older macOS must upgrade or stay on an older file-librarian.
- Adding a language is one entry in `locales.py` plus a test.
- Renaming a category or the review folder later still needs an explicit
  migration (RF-37), whatever the language.
