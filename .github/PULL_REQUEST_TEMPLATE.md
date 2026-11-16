## What and why

<!-- One or two sentences. Link the issue and the requirement: Closes #123, RF-xx. -->

## How it was tested

<!-- Tests added or changed. Tests must stay offline and use temporary folders. -->

## Checklist

- [ ] `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy` and `uv run pytest` pass
- [ ] Nothing can delete or overwrite a user file, and every new operation can be undone
- [ ] No real personal files, paths or keys in code, tests or fixtures
- [ ] `CHANGELOG.md` updated under `Unreleased` (if user-visible)
- [ ] ADR added in `docs/decisions/` (if the architecture changes)
