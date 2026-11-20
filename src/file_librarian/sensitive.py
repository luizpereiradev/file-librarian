"""Secret detection before anything is sent to a provider (RF-28).

A file is *sensitive* when its name looks like a key or credential store, or
its extracted text contains something that looks like a secret. Sensitive
files are never sent, get no description and stay where they are.

Findings are reported by kind only ("private key", "AWS access key"); the
matched value is never returned, logged or stored. False positives are
acceptable here: the cost is a file left in place, not a leaked key. Names
alone are matched conservatively: ``*.key`` is left out because Keynote uses
it; a real key file is still caught by its PEM header.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from file_librarian.globs import matches_any

SENSITIVE_NAMES = (
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "*.pem", "*.p8",
    "*.p12", "*.pfx", "*.jks", "*.keystore", "*.kdbx", "*.keychain", "*.keychain-db",
    "*.ovpn", ".env", ".env.*", "*.env", ".netrc", ".pgpass", ".npmrc", ".pypirc",
    "credentials", "credentials.json", "*credentials*.json", "service-account*.json",
)  # fmt: skip

# (kind, pattern) pairs. Public so the diagnostics report (doctor.py) redacts the same
# secrets this module detects; callers report the kind, never the matched value.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private key", re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY(?: BLOCK)?-----")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_\w{40,})")),
    ("GitLab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("Stripe key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{20,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}")),
    (
        "JSON web token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ),
    ("connection string with password", re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]{4,}@")),
    (
        "password or secret assignment",
        re.compile(
            r"(?im)^\s*(?:export\s+)?[\w.-]*(?:password|passwd|secret|api[_-]?key|access[_-]?token"
            r"|private[_-]?key|client[_-]?secret)[\w.-]*\s*[:=]\s*['\"]?[^\s'\"#]{8,}"
        ),
    ),
)  # fmt: skip

# An e-mail address. Not a secret (a file holding one is not sensitive), but the
# diagnostics report (doctor.py) and the description scrubber (privacy.py) replace it.
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def sensitive_name(name: str) -> bool:
    return matches_any(name, SENSITIVE_NAMES)


def find_secrets(text: str) -> list[str]:
    """Kinds of secrets found in ``text``, in a stable order, without the values."""
    return [kind for kind, pattern in SECRET_PATTERNS if pattern.search(text)]


def withheld_by_config(name: str, never_send: Iterable[str]) -> bool:
    """Files the user chose to classify from metadata only."""
    return matches_any(name, never_send)
