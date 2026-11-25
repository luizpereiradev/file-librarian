# Prototypes: not shipped

Throwaway code from spikes, kept so the issue that follows can start from it.
Nothing here is part of the `file-librarian` package: the wheel only packages
`src/file_librarian`, and lint, type checks and coverage only look at Python
under `src/` and `tests/`. Do not import from here.

| Folder | Spike | Decision |
| --- | --- | --- |
| `menu-bar/` | #111, a menu bar item without Xcode | [ADR 0004](../docs/decisions/0004-menu-bar-item.md) |
