"""Measuring classification quality: a synthetic corpus, gold labels and ``flib eval``.

- ``documents``: byte-for-byte reproducible synthetic Office, PDF and e-mail files;
- ``corpus``: the public evaluation corpus and its gold labels (``gold.json``);
- ``harness``: runs the corpus through the real pipeline in simulate mode, in a
  temporary folder with a throwaway database, and scores the result;
- ``score``: the scoring math, the saved-result format and ``--compare``.

See docs/quality.md.
"""
