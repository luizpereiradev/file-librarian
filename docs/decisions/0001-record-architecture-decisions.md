# 1. Record architecture decisions

- Status: accepted
- Date: 2026-11-16

## Context

file-librarian is meant to be easy for outside contributors to extend. People
arriving later need to know why the code is shaped the way it is, especially
where it trades convenience for safety.

## Decision

Architecture changes are recorded as Architecture Decision Records in
`docs/decisions/`, numbered `NNNN-short-title.md`, before the code lands. Use
[`template.md`](template.md). An accepted ADR is not rewritten; a later ADR
supersedes it.

Decisions already fixed by the PRD and recorded here as the starting point:

- Python 3.11+, SQLite with FTS5, macOS only, `flib` as the command.
- Claude (`anthropic` provider) is the default; providers are pluggable.
- The AI only returns data; deterministic code moves files. Nothing is deleted
  or overwritten, and every operation can be undone.
- Code, comments, messages and repository documentation are in English.

## Consequences

Every architecture pull request links an ADR. Small changes that do not alter
the architecture do not need one.
