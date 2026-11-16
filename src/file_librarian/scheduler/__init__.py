"""Daily run as a launchd agent (RF-19). Lands in M2.

``flib schedule`` writes a LaunchAgent plist, checks that the scheduled process
can read the input folders, and explains how to grant access if it cannot.
"""
