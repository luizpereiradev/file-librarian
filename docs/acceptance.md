# M1 acceptance with the real provider

The automated test `tests/acceptance/test_m1_exit_criterion.py` checks the M1
exit criterion offline. This is the same check with Claude, run by a
maintainer before calling M1 done. It costs well under one cent with the
default model and never touches your real Downloads folder.

## 1. Build a test folder

```sh
mkdir -p /tmp/flib-acceptance/inbox
uv run python -c "
from pathlib import Path
import tests.acceptance.test_m1_exit_criterion as t
print(t.build_corpus(Path('/tmp/flib-acceptance/inbox')))
"
```

That creates 50 items: PDFs, Office files, images, installers, archives, a
legacy `.doc`, an encrypted PDF, a sub-folder, a `.app` bundle and two
credential files. Their modification times are set three hours back, so they
count as stable.

## 2. Point a config at it

`/tmp/flib-acceptance/config.yaml`:

```yaml
language: pt-BR
inputs: [/tmp/flib-acceptance/inbox]
output_root: /tmp/flib-acceptance/inbox/Organizado
database: /tmp/flib-acceptance/library.db
provider:
  name: anthropic
  model: claude-haiku-5-5
limits:
  max_cost_usd_per_run: 0.10
```

## 3. Run, search, undo

```sh
export FLIB_CONFIG=/tmp/flib-acceptance/config.yaml
uv run flib check-config
uv run flib simulate                       # read the plan; nothing moves
uv run flib run                            # note the estimated cost line
uv run flib search "fatura energia"
uv run flib search "contrato aluguel" --json
uv run flib undo
```

## 4. Pass criteria

- `flib run` reports 0 failed, and the two credential files are reported as
  sensitive and left in place.
- The `.app` and the `thesis` folder are moved whole.
- Names and descriptions are in Portuguese, and the categories make sense
  (record any that are wrong in the PR that closes M1).
- The searches return the expected files.
- After `flib undo`, `inbox/` holds exactly the original 50 items.
- The estimated cost is below the cap.

Then delete `/tmp/flib-acceptance`.
