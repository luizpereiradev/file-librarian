"""Secret detection before anything is sent to a provider (RF-28).

A file is *sensitive* when its name looks like a key or credential store, or
its extracted text contains something that looks like a secret. Sensitive
files are never sent, get no description and stay where they are.

Findings are reported by kind only ("private key", "AWS access key"); the
matched value is never returned, logged or stored. False positives are
acceptable here: the cost is a file left in place, not a leaked key. Names
alone are matched conservatively: ``*.key`` is left out because Keynote uses
it; a real key file is still caught by its PEM header.

Names that say they hold passwords, keys, tokens or recovery material are sensitive too,
whatever their content (``sensitive_words``). They are matched by whole words,
never inside another word: the name is cut into words at anything that is not
a letter or digit and where a lower-case letter meets an upper-case one
(``ChromePasswords``), never between a digit and a letter (``1Password`` is
one word, so an installer called ``1Password-8.10.dmg`` is not caught, nor is
a hexadecimal name with ``2fa`` inside). Installers and apps (``.dmg``,
``.pkg``, ``.app``...) are never sensitive by name alone. Some harmless files
are still caught (a "password reset guide.pdf"); they are left where they are,
``sensitive.allow_names`` lets them through, and a file whose name alone made
it sensitive is looked at again once renamed.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from file_librarian.globs import matches_any

SENSITIVE_NAMES = (
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "*.pem", "*.p8",
    "*.p12", "*.pfx", "*.jks", "*.keystore", "*.kdbx", "*.keychain", "*.keychain-db",
    "*.ovpn", ".env", ".env.*", "*.env", ".netrc", ".pgpass", ".npmrc", ".pypirc",
    "credentials", "credentials.json", "*credentials*.json", "service-account*.json",
    "logins.csv",  # the export of Firefox's and other browsers' saved passwords
    # OAuth client files as the cloud consoles name them, and other stores of tokens.
    "client_secret*", "client-secret*", "token.json", "tokens.json", "secrets.json",
    "secrets.y*ml", ".htpasswd", "kubeconfig", "*.kubeconfig", "*.tfstate", "*.tfvars",
)  # fmt: skip

# Words (or runs of words) that say a file holds passwords or recovery material.
SENSITIVE_WORDS: tuple[tuple[str, ...], ...] = (
    ("password",), ("passwords",), ("passwd",), ("2fa",),
    ("senha",), ("senhas",), ("contraseña",), ("contraseñas",),
    ("passwort",), ("passwörter",), ("passwoerter",),
    ("mot", "de", "passe"), ("mots", "de", "passe"),
    ("recovery", "code"), ("recovery", "codes"), ("backup", "code"), ("backup", "codes"),
    ("seed", "phrase"), ("private", "key"), ("chave", "privada"),
    ("código", "de", "recuperação"), ("codigo", "de", "recuperacao"),
    ("códigos", "de", "recuperação"), ("codigos", "de", "recuperacao"),
    ("bitwarden", "export"),
    ("client", "secret"), ("client", "secrets"), ("api", "key"), ("api", "keys"),
    ("access", "token"), ("refresh", "token"), ("secret", "key"),
)  # fmt: skip
# "recovery key" names a manual or a part as often as a key: only at the end of the
# name (or before a number), or in a text-like file.
_RECOVERY_KEY = ("recovery", "key")
TEXT_LIKE = frozenset({".txt", ".text", ".md", ".rtf", ""})
NEVER_BY_NAME = frozenset({
    ".dmg", ".pkg", ".mpkg", ".app", ".ipa", ".apk", ".exe", ".msi", ".xip",
})  # fmt: skip
"""Installers and apps: never sensitive by their name alone."""

_RUN = re.compile(r"[^\W_]+")


def name_words(name: str) -> list[str]:
    """The words of a file name (without its extension), lower case.

    Cut at anything that is not a letter or digit and where a lower-case letter
    is followed by an upper-case one; never between a digit and a letter.
    """
    stem, _ = _split(unicodedata.normalize("NFC", name))
    words: list[str] = []
    for run in _RUN.findall(stem):
        start = 0
        for i in range(1, len(run)):
            if run[i - 1].islower() and run[i].isupper():
                words.append(run[start:i].casefold())
                start = i
        words.append(run[start:].casefold())
    return words


def _split(name: str) -> tuple[str, str]:
    dot = name.rfind(".")
    if dot <= 0:
        return name, ""
    return name[:dot], name[dot:].casefold()


def _contains(words: list[str], phrase: tuple[str, ...]) -> list[int]:
    """Where ``phrase`` starts in ``words``."""
    n = len(phrase)
    return [i for i in range(len(words) - n + 1) if tuple(words[i : i + n]) == phrase]


def sensitive_reason(name: str, allow: Iterable[str] = ()) -> str | None:
    """Why ``name`` alone makes a file sensitive (``None``: it does not).

    ``allow``: names the user declared fine (``sensitive.allow_names``).
    """
    if allow and matches_any(name, allow):
        return None
    if matches_any(name, SENSITIVE_NAMES):
        return "a credential file"
    extension = _split(unicodedata.normalize("NFC", name))[1]
    if extension in NEVER_BY_NAME:
        return None
    words = name_words(name)
    for phrase in SENSITIVE_WORDS:
        if _contains(words, phrase):
            return " ".join(phrase)
    for i in _contains(words, _RECOVERY_KEY):
        after = words[i + 2 :]
        if not after or after[0].isdigit() or extension in TEXT_LIKE:
            return "recovery key"
    return None


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
    ("Google OAuth client secret", re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{20,}")),
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
    (
        # The same keys as a quoted JSON or YAML key, anywhere on a line: a credentials
        # file is often one long line, which the assignment pattern above never matches.
        "password or secret in JSON",
        re.compile(
            r"(?i)[\"'][\w.-]*(?:password|passwd|secret|api[_-]?key|access[_-]?token"
            r"|refresh[_-]?token|private[_-]?key)[\w.-]*[\"']\s*:\s*[\"'][^\"'\s]{8,}[\"']"
        ),
    ),
)  # fmt: skip

# An e-mail address. Not a secret (a file holding one is not sensitive), but the
# diagnostics report (doctor.py) and the description scrubber (privacy.py) replace it.
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def name_sensitive(reason: str) -> str:
    """Why a file is sensitive because of its name, in words (``sensitive_reason``'s)."""
    text = "name looks like a credential file"
    return text if reason == "a credential file" else f"{text}: {reason}"


def sensitive_name(name: str, allow: Iterable[str] = ()) -> bool:
    """``name`` alone makes a file sensitive (see ``sensitive_reason``)."""
    return sensitive_reason(name, allow) is not None


def find_secrets(text: str) -> list[str]:
    """Kinds of secrets found in ``text``, in a stable order, without the values."""
    return [kind for kind, pattern in SECRET_PATTERNS if pattern.search(text)]


def withheld_by_config(name: str, never_send: Iterable[str]) -> bool:
    """Files the user chose to classify from metadata only."""
    return matches_any(name, never_send)
