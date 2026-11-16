"""Stage 1: find files that still need processing. Lands in M1.

- Lists the input folders and applies ``ignore`` globs and system files (RF-03).
- Skips downloads in progress and waits until size and mtime are stable (RF-02).
- Treats sub-folders, ``.app`` bundles and packages as one unit (RF-23).
- Drops anything whose sha256 is already in the database (RF-01).
- Skips iCloud files that are only in the cloud, without downloading them (RF-24).
"""
