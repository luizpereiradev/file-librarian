# Releasing

Releases are cut by hand by the maintainer. There is no release workflow, and
nothing is published to PyPI: users install from the repository with
`./scripts/install.sh`, which installs exactly the versions in `uv.lock`. A
release is a tag on `main` plus a GitHub release with notes from the
CHANGELOG.

## Checklist

Replace `X.Y.Z` with the new version ([Semantic Versioning](https://semver.org/);
before 1.0, a minor bump may break things and must say so).

1. **`main` is green.** The latest commit on `main` passed CI (the `CI passed`
   check, which on `main` covers Python 3.11 through 3.15) and the Security
   workflow. Do not release from a commit whose checks are still running.
2. **Try it.** On a clean clone of that commit: `./scripts/install.sh`, then
   `flib --version`, `flib check-config` and `flib simulate` on your own
   configuration. For a release that changes classification or moving, run
   the [acceptance check](acceptance.md).
3. **Prepare the release pull request** (branch `chore/release-vX.Y.Z`):
   - In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`
     (today's date), add a new empty `## [Unreleased]` above it, and update
     the link references at the bottom:

     ```markdown
     [Unreleased]: https://github.com/luizpereiradev/file-librarian/compare/vX.Y.Z...HEAD
     [X.Y.Z]: https://github.com/luizpereiradev/file-librarian/releases/tag/vX.Y.Z
     ```

     (for a later release, `[X.Y.Z]` compares the previous tag with `vX.Y.Z`).
   - Set `version = "X.Y.Z"` in `pyproject.toml` if it is not already, then
     `uv lock` so `uv.lock` records it (only the `file-librarian` entry may
     change; `uv lock --check` must pass).
   - If the release ends a "Development Status" stage, update that classifier.
   - Title the pull request `chore: release vX.Y.Z`, wait for `CI passed`,
     and squash-merge it.
4. **Tag the merge commit** on `main`, not the branch:

   ```sh
   git switch main && git pull --ff-only
   uv run flib --version          # must print flib X.Y.Z
   git tag -s vX.Y.Z -m "vX.Y.Z"  # signed (needs git signing set up)
   git push origin vX.Y.Z
   ```

   Without signing set up, use an annotated tag instead:
   `git tag -a vX.Y.Z -m "vX.Y.Z"`. Never a lightweight tag.

5. **Publish the GitHub release** from the tag, with the version's CHANGELOG
   section as the notes:

   ```sh
   gh release create vX.Y.Z --title "vX.Y.Z" --notes-file notes.md --verify-tag
   ```

   where `notes.md` is the `## [X.Y.Z]` section without its heading. Add
   `--prerelease` while the version is below 1.0 if you want GitHub to flag
   it. Keep the install instructions pointing at the repository.
6. **Afterwards:** check the release page renders, and that a fresh
   `git clone` + `git checkout vX.Y.Z` + `./scripts/install.sh` works.

## Do not

- **Do not publish to PyPI**, from a laptop or otherwise. PyPI publishing is
  planned for v1.0, through a dedicated workflow with Trusted Publishing
  ([ADR 0003](decisions/0003-supply-chain.md)). Until then the only install
  path is the repository and `scripts/install.sh`.
- **Do not add a release workflow** or upload build artifacts to the release;
  the source archive GitHub attaches is enough.
- **Do not move or delete a pushed tag.** If a release is broken, fix it on
  `main` and release `X.Y.Z+1`.
- **Do not bump dependencies in the release pull request.** It changes the
  CHANGELOG, the version and the lock's own entry, nothing else.
- **Do not release with the quarantine bypassed.** An `exclude-newer-package`
  exception from [Urgent security fixes](../SECURITY.md#urgent-security-fixes)
  may ship if the advisory needs it, but say so in the release notes.
