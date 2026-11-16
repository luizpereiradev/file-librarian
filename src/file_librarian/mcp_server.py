"""Local MCP server over stdio (RF-22). Lands in M2.

Read tools: search_files, get_file, list_recent, list_review, get_stats.
Write tools (off when ``mcp.read_only``): recategorize_file, undo. Writes go
through the planner and executor like any other run. Responses carry metadata
and descriptions only, never file content, and descriptions are untrusted data.
"""
