"""``flib ui``: the local interface (#113), a web server that exists only while in use.

``server.py`` is the server, its session and its security checks; ``api.py``
builds what the JSON API returns; ``pages/`` and ``static/`` are the HTML, CSS
and JavaScript, shipped in the package and served from memory (no build step,
nothing from a CDN). Standard library only.
"""
