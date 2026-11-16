"""Stage 5: apply a plan. Lands in M1.

Takes the single-instance lock (RF-34), records each operation as ``planned``
before touching the disk, moves without ever overwriting (RF-11), then marks
it ``done``. Cross-volume moves copy, verify the hash, and only then remove the
source (RF-33). Nothing is ever deleted otherwise.
"""
