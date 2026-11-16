"""Stage 4: turn classifications into a plan of operations. Lands in M1.

Applies the naming pattern and each category's rename policy (RF-10, RF-31,
RF-32), resolves name collisions with a numeric suffix, keeps destinations
inside the configured category folders, and sends low-confidence files to the
review folder (RF-09). ``flib simulate`` stops here and prints the plan (RF-16).
"""
